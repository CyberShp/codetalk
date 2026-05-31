"""Regression tests for the 2026-05-31 Round 4 buglist fixes.

Focus: GitNexus same-name repo disambiguation must not only skip re-analyze but
also fetch the RIGHT repo's graph (Round 4 P1).  Plus the exact-symbol local
recall safety net and the enriched 00 index-coverage table.
"""

import asyncio
import gc
import json
import subprocess

import pytest

from app.adapters.base import AnalysisRequest
from app.adapters.cgc import CGCAdapter
from app.adapters.gitnexus import resolve_indexed_repo, resolve_indexed_repo_name
from app.llm.base import (
    BaseLLMClient,
    LLMResponse,
    current_finish_reason,
    current_task_id,
    get_truncation_count,
    note_truncation,
    reset_truncation_count,
)
from app.schemas.workspace_analysis import (
    AnalysisObject,
    AnalysisPlan,
    ResolvedAnalysisObject,
    ReportSpec,
    ScopeCandidate,
    LLMLimits,
    build_default_plan,
)
from app.services.analysis_pipeline import AnalysisPipeline
from app.services.evidence_card_builder import (
    EvidenceCard,
    EvidenceCardBuilder,
    _read_symbol_window_from_text,
)
from app.services.report_generator import (
    ReportGenerator,
    _section_is_invalid,
    _plan_report_filename,
    build_index_coverage_section,
    scrub_bdev_source_false_gaps,
)
from app.services.workspace_scope_resolver import (
    _exact_symbol_repo_hits_blocking,
    _looks_like_symbol,
    _path_hint_repo_hits_blocking,
)


# Real Round-4 /api/repos payload shape: top-level array, two `spdk`.
_DUP_PAYLOAD = [
    {"name": "spdk", "path": r"D:\coworkers\spdk",
     "stats": {"files": 1936, "nodes": 50259, "edges": 101329}},
    {"name": "spdk", "path": r"E:\codetalk_test\codetalks-Test\fixtures\spdk",
     "stats": {"files": 1931, "nodes": 50401, "edges": 101699}},
]
_TARGET = r"E:\codetalk_test\codetalks-Test\fixtures\spdk"


def test_descriptor_resolves_target_with_stats_and_ambiguity() -> None:
    d = resolve_indexed_repo(_DUP_PAYLOAD, _TARGET)
    assert d is not None
    assert d["name"] == "spdk"
    assert d["ambiguous"] is True
    assert d["node_count"] == 50401 and d["edge_count"] == 101699
    assert d["path"] == _TARGET


def test_descriptor_none_for_unknown_target() -> None:
    assert resolve_indexed_repo(_DUP_PAYLOAD, r"X:\nope\spdk") is None
    assert resolve_indexed_repo_name(_DUP_PAYLOAD, r"X:\nope\spdk") is None


def test_looks_like_symbol() -> None:
    assert _looks_like_symbol("spdk_log_set_flag") is True
    assert _looks_like_symbol("SPDK_NOTICELOG") is True
    assert _looks_like_symbol("a long natural language scenario") is False
    assert _looks_like_symbol("鏃ュ織妯″潡璁茶В") is False


def test_exact_symbol_hits_prioritize_implementation_files() -> None:
    hits = _exact_symbol_repo_hits_blocking(_TARGET, "spdk_log_open", 5)
    assert hits
    assert hits[0].replace("\\", "/").endswith("/lib/log/log.c")

    hits = _exact_symbol_repo_hits_blocking(_TARGET, "spdk_log_set_flag", 5)
    assert hits
    assert hits[0].replace("\\", "/").endswith("/lib/log/log_flags.c")

    hits = _exact_symbol_repo_hits_blocking(_TARGET, "spdk_log_deprecated", 5)
    assert hits
    assert hits[0].replace("\\", "/").endswith("/lib/log/log_deprecated.c")


def test_path_hints_prioritize_exact_source_files() -> None:
    hits = _path_hint_repo_hits_blocking(
        _TARGET,
        ["lib/log/log_flags.c", "lib/log/log_deprecated.c", "missing/nope.c"],
        5,
    )
    normalized = [h.replace("\\", "/") for h in hits]
    assert normalized[0].endswith("/lib/log/log_flags.c")
    assert normalized[1].endswith("/lib/log/log_deprecated.c")
    assert all("missing/nope.c" not in h for h in normalized)


def test_symbol_evidence_snippet_focuses_on_definition() -> None:
    limits = LLMLimits(max_files_per_object=2, max_evidence_cards=4)
    builder = EvidenceCardBuilder(repo_path=_TARGET, limits=limits)
    resolved = ResolvedAnalysisObject(
        object_id="obj_flag",
        text="spdk_log_set_flag",
        candidate_files=[
            ScopeCandidate(
                path=r"lib\log\log_flags.c",
                symbol="spdk_log_set_flag",
                source="repo_search",
                confidence="high",
                reason="鏈湴婧愮爜绮剧‘绗﹀彿鍛戒腑",
            )
        ],
    )
    cards = asyncio.run(builder.build_cards([resolved]))
    assert cards
    assert cards[0].symbol == "spdk_log_set_flag"
    assert "spdk_log_set_flag(const char *name)" in cards[0].snippet


def test_symbol_window_prefers_function_definition_over_earlier_call_site() -> None:
    source = (
        "void\n"
        "spdk_log(enum spdk_log_level level)\n"
        "{\n"
        "\tspdk_vlog(level, file, line, func, format, ap);\n"
        "}\n\n"
        + "\n".join(f"/* padding {i} */" for i in range(30)) + "\n"
        "void\n"
        "spdk_vlog(enum spdk_log_level level, const char *file, const int line, const char *func,\n"
        "\t  const char *format, va_list ap)\n"
        "{\n"
        "\tchar *ext_buf = NULL;\n"
        "\trc = vasprintf(&ext_buf, format, ap_copy);\n"
        "\tfree(ext_buf);\n"
        "}\n"
    )

    snippet = _read_symbol_window_from_text(source, "spdk_vlog", 4000)

    assert "spdk_vlog(enum spdk_log_level level" in snippet
    assert "vasprintf(&ext_buf" in snippet
    assert "free(ext_buf)" in snippet


def test_evidence_builder_reads_source_through_gitnexus_first(monkeypatch) -> None:
    calls = []
    cli_calls = []

    async def _empty_cli(**kwargs):
        cli_calls.append(kwargs)
        return "", ""

    monkeypatch.setattr("app.services.evidence_card_builder._read_snippet_from_gitnexus_cli", _empty_cli)

    class _GitNexusFileResp:
        status_code = 200

        def json(self):
            return {
                "content": (
                    "static void helper(void) {}\n"
                    "void\n"
                    "spdk_log_deprecated(struct spdk_deprecation *dep, const char *file, uint32_t line, const char *func)\n"
                    "{\n"
                    "\tdep->hits++;\n"
                    "}\n"
                )
            }

        def raise_for_status(self):
            return None

    class _GitNexusFileClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, path, params=None):
            calls.append((path, dict(params or {})))
            return _GitNexusFileResp()

    monkeypatch.setattr("app.services.evidence_card_builder.httpx.AsyncClient", _GitNexusFileClient)

    limits = LLMLimits(max_files_per_object=2, max_evidence_cards=4)
    builder = EvidenceCardBuilder(
        repo_path=_TARGET,
        limits=limits,
        gitnexus_repo="spdk",
    )
    resolved = ResolvedAnalysisObject(
        object_id="obj_deprecated",
        text="spdk_log_deprecated",
        candidate_files=[
            ScopeCandidate(
                path=r"lib\log\log_deprecated.c",
                symbol="spdk_log_deprecated",
                source="repo_search",
                confidence="high",
                reason="绮剧‘绗﹀彿鍛戒腑",
            )
        ],
    )
    cards = asyncio.run(builder.build_cards([resolved]))
    assert cli_calls
    assert calls
    assert calls[0][0] == "/api/file"
    assert calls[0][1]["repo"] == _TARGET
    assert calls[0][1]["path"] == "lib/log/log_deprecated.c"
    assert "spdk_log_deprecated(struct spdk_deprecation *dep" in cards[0].snippet
    assert any("GitNexus /api/file" in note for note in cards[0].notes)


def test_evidence_builder_prefers_gitnexus_cli_source_before_http(monkeypatch) -> None:
    http_calls = []
    cli_calls = []

    async def _cli_source(**kwargs):
        cli_calls.append(kwargs)
        return (
            "void\n"
            "spdk_log_set_flag(const char *name)\n"
            "{\n"
            "\tlog_set_flag(name, true);\n"
            "}\n",
            "source read through GitNexus CLI: lib/log/log_flags.c",
        )

    class _HttpClientMustNotRun:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, path, params=None):
            http_calls.append((path, dict(params or {})))
            raise AssertionError("HTTP /api/file should not run after CLI source succeeds")

    monkeypatch.setattr("app.services.evidence_card_builder._read_snippet_from_gitnexus_cli", _cli_source)
    monkeypatch.setattr("app.services.evidence_card_builder.httpx.AsyncClient", _HttpClientMustNotRun)

    limits = LLMLimits(max_files_per_object=2, max_evidence_cards=4)
    builder = EvidenceCardBuilder(
        repo_path=_TARGET,
        limits=limits,
        gitnexus_repo="spdk",
    )
    resolved = ResolvedAnalysisObject(
        object_id="obj_flag",
        text="spdk_log_set_flag",
        candidate_files=[
            ScopeCandidate(
                path=r"lib\log\log_flags.c",
                symbol="spdk_log_set_flag",
                source="repo_search",
                confidence="high",
                reason="exact symbol hit",
            )
        ],
    )

    cards = asyncio.run(builder.build_cards([resolved]))

    assert cli_calls
    assert cli_calls[0]["symbol"] == "spdk_log_set_flag"
    assert cli_calls[0]["repo_path"] == _TARGET
    assert "log_set_flag(name, true)" in cards[0].snippet
    assert any("GitNexus CLI" in note for note in cards[0].notes)
    assert http_calls == []


def test_gitnexus_cli_source_reader_uses_file_command_before_context(monkeypatch) -> None:
    from app.services import evidence_card_builder as ecb

    commands = []

    class _Proc:
        returncode = 0
        stderr = ""
        stdout = (
            '{"repo":"E:/repo/spdk","path":"lib/log/log_deprecated.c",'
            '"content":"void\\nspdk_log_deprecated(struct spdk_deprecation *dep)\\n{\\n\\tdep->hits++;\\n}\\n"}'
        )

    def _run(cmd, **kwargs):
        commands.append(cmd)
        return _Proc()

    monkeypatch.setattr(ecb, "_resolve_gitnexus_cli_bin", lambda: "gitnexus")
    monkeypatch.setattr(ecb.subprocess, "run", _run)

    text, note = ecb._read_snippet_from_gitnexus_cli_blocking(
        repo_name="spdk",
        repo_path=r"E:\repo\spdk",
        rel_path="lib/log/log_deprecated.c",
        symbol="spdk_log_deprecated",
    )

    assert commands
    assert commands[0][:5] == [
        "gitnexus",
        "file",
        "-r",
        r"E:\repo\spdk",
        "lib/log/log_deprecated.c",
    ]
    assert "dep->hits++" in text
    assert "GitNexus CLI file" in note


def test_unit_prompt_card_selection_keeps_exact_symbol_evidence() -> None:
    pipe = AnalysisPipeline()
    noisy_cards = [
        EvidenceCard(
            card_id=f"noise_{i}",
            object_id="obj_open",
            title=f"璋冪敤渚у櫔澹?{i}",
            source="gitnexus",
            confidence="medium",
            file_path=rf"E:\repo\app\caller_{i}.c",
        )
        for i in range(8)
    ]
    important = EvidenceCard(
        card_id="flag_impl",
        object_id="obj_flag",
        title="浠ｇ爜璇佹嵁锛歭og_flags.c",
        source="repo_search",
        confidence="high",
        file_path=r"E:\repo\lib\log\log_flags.c",
        symbol="spdk_log_set_flag",
    )
    deprecated = EvidenceCard(
        card_id="deprecated_impl",
        object_id="obj_deprecated",
        title="浠ｇ爜璇佹嵁锛歭og_deprecated.c",
        source="repo_search",
        confidence="high",
        file_path=r"E:\repo\lib\log\log_deprecated.c",
        symbol="spdk_log_deprecated",
    )
    cards = pipe._cards_for_unit_prompt({
        "object_ids": ["obj_open", "obj_flag", "obj_deprecated"],
        "cards": noisy_cards + [important, deprecated],
    }, limit=6)
    paths = [c.file_path for c in cards]
    assert important.file_path in paths
    assert deprecated.file_path in paths


def test_report_section_card_selection_keeps_exact_symbol_evidence() -> None:
    noisy_cards = [
        EvidenceCard(
            card_id=f"noise_{i}",
            object_id="obj_open",
            title=f"璋冪敤渚у櫔澹?{i}",
            source="gitnexus",
            confidence="medium",
            file_path=rf"E:\repo\app\caller_{i}.c",
        )
        for i in range(12)
    ]
    flag_impl = EvidenceCard(
        card_id="flag_impl",
        object_id="obj_flag",
        title="浠ｇ爜璇佹嵁锛歭og_flags.c",
        source="repo_search",
        confidence="high",
        file_path=r"E:\repo\lib\log\log_flags.c",
        symbol="spdk_log_set_flag",
        snippet="90: int\n91: spdk_log_set_flag(const char *name)",
    )
    deprecated_impl = EvidenceCard(
        card_id="deprecated_impl",
        object_id="obj_deprecated",
        title="浠ｇ爜璇佹嵁锛歭og_deprecated.c",
        source="repo_search",
        confidence="high",
        file_path=r"E:\repo\lib\log\log_deprecated.c",
        symbol="spdk_log_deprecated",
        snippet="77: void\n78: spdk_log_deprecated(struct spdk_deprecation *dep, const char *file, uint32_t line, const char *func)",
    )
    selected = ReportGenerator._select_report_section_cards(
        [{
            "object_ids": ["obj_open", "obj_flag", "obj_deprecated"],
            "cards": noisy_cards + [flag_impl, deprecated_impl],
        }],
        limit=6,
    )
    paths = [c.file_path for c in selected]
    assert flag_impl.file_path in paths
    assert deprecated_impl.file_path in paths
    exact = ReportGenerator._format_exact_source_evidence(selected)
    assert "spdk_log_deprecated" in exact
    assert "证据卡未提供实现源码" in exact


def test_exact_source_evidence_summarizes_deprecated_source_truth() -> None:
    card = EvidenceCard(
        card_id="deprecated_impl",
        object_id="obj_deprecated",
        title="浠ｇ爜璇佹嵁锛歭og_deprecated.c",
        source="repo_search",
        confidence="high",
        file_path=r"E:\repo\lib\log\log_deprecated.c",
        symbol="spdk_log_deprecated",
        snippet=(
            "87: dep->hits++;\n"
            "89: if (dep->interval != 0) {\n"
            "91:     dep->deferred++;\n"
            "98: spdk_log(SPDK_LOG_WARN, file, line, func, ...);\n"
        ),
    )

    exact = ReportGenerator._format_exact_source_evidence([card])

    assert "SPDK_LOG_WARN" in exact
    assert "dep->interval" in exact
    assert "未见 1000" in exact
    assert "不要写成通过 `SPDK_NOTICELOG` 输出" in exact


def test_log_source_truth_scrubs_false_unverified_wording() -> None:
    pinned = (
        "> Pinned exact source facts (deterministic, do not omit):\n"
        "> - `spdk_log_deprecated`: 每次调用累加 `dep->hits++`；"
        "源码调用 `SPDK_LOG_WARN`；未见 1000 整数倍阈值检查；"
        "不要写成通过 `SPDK_NOTICELOG` 输出"
    )
    body = (
        "源码证据：`lib/log/log_deprecated.c` 中 `spdk_log_deprecated` "
        "使用 `SPDK_LOG_WARN` 而非 `SPDK_NOTICELOG`，无 1000-hit 阈值。"
        "**待源码验证**。\n"
        "保留这一行：设计需求与源码事实冲突，验收会失败。"
    )
    scrubbed = ReportGenerator._scrub_source_false_gaps(body, pinned)
    assert "待源码验证" not in scrubbed
    assert "源码证据" not in scrubbed
    assert "保留这一行" in scrubbed


def test_exact_source_evidence_summarizes_vlog_ext_buf_release() -> None:
    card = EvidenceCard(
        card_id="vlog_impl",
        object_id="obj_vlog",
        title="代码证据：log.c",
        source="repo_search",
        confidence="high",
        file_path=r"E:\repo\lib\log\log.c",
        symbol="spdk_vlog",
        snippet=(
            "197: char *ext_buf = NULL;\n"
            "199: buf = _buf;\n"
            "203: if (rc > MAX_TMPBUF) {\n"
            "207:     rc = vasprintf(&ext_buf, format, ap_copy);\n"
            "211:     buf = ext_buf;\n"
            "233: free(ext_buf);\n"
        ),
    )

    exact = ReportGenerator._format_exact_source_evidence([card])

    assert "vasprintf" in exact
    assert "free(ext_buf)" in exact
    assert "ext_buf` 初始化为 `NULL" in exact
    assert "不要写成 `ext_buf` 未释放" in exact
    assert "不要推测 `ext_buf` 未定义或 wild pointer" in exact


def test_vlog_file_source_summary_reads_free_ext_buf_window() -> None:
    card = EvidenceCard(
        card_id="card-vlog-file",
        object_id="obj_vlog_file",
        title="代码证据：log.c",
        source="repo_search",
        file_path=str(_TARGET + r"\lib\log\log.c"),
        symbol=None,
        snippet="char *buf, _buf[MAX_TMPBUF], *ext_buf = NULL;\nvasprintf(&ext_buf, format, ap_copy);",
        confidence="high",
    )
    facts = ReportGenerator._summarize_source_facts(card)
    assert "free(ext_buf)" in facts
    assert "不要写成 `ext_buf` 未释放或存在内存泄漏" in facts


def test_vlog_file_source_summary_captures_vasprintf_failure_truncation() -> None:
    card = EvidenceCard(
        card_id="card-vlog-vasprintf-fail",
        object_id="obj_vlog_file",
        title="代码证据：log.c",
        source="repo_search",
        file_path=str(_TARGET + r"\lib\log\log.c"),
        symbol="spdk_vlog",
        snippet="rc = vasprintf(&ext_buf, format, ap_copy);",
        confidence="high",
    )
    facts = ReportGenerator._summarize_source_facts(card)
    assert "vasprintf 失败时保留栈缓冲截断内容继续输出" in facts
    assert "不要写成 `vasprintf` 失败后直接返回或不输出日志" in facts


def test_vlog_source_truth_scrubs_false_leak_wording() -> None:
    pinned = (
        "> Pinned exact source facts (deterministic, do not omit):\n"
        "> - `代码证据：log.c`: 源码将 `ext_buf` 初始化为 `NULL`；"
        "源码在长消息分支调用 `vasprintf(&ext_buf, ...)`；"
        "源码在函数返回前调用 `free(ext_buf)` 释放堆缓冲；"
        "不要写成 `ext_buf` 未释放或存在内存泄漏"
    )
    body = (
        "| `spdk_vlog` 中 `vasprintf` 失败时 `ext_buf` 泄漏 | 高 |\n"
        "保留这一行：长消息分支使用 `vasprintf`。"
    )
    scrubbed = ReportGenerator._scrub_source_false_gaps(body, pinned)
    assert "泄漏" not in scrubbed
    assert "保留这一行" in scrubbed


def test_vlog_source_truth_scrubs_false_vasprintf_failure_return() -> None:
    pinned = (
        "> Pinned exact source facts (deterministic, do not omit):\n"
        "> - `spdk_vlog`: 源码在长消息分支调用 `vasprintf(&ext_buf, ...)`；"
        "`vasprintf` 失败时保留栈缓冲截断内容继续输出；"
        "不要写成 `vasprintf` 失败后直接返回或不输出日志"
    )
    body = (
        "| `spdk_vlog` vasprintf 失败 | 模拟 `vasprintf` 返回 < 0 | "
        "`rc < 0` 时 `spdk_vlog` 返回 | 不输出日志，函数返回 | 无日志输出 |\n"
        "保留这一行：长消息分支使用 `vasprintf`。"
    )
    scrubbed = ReportGenerator._scrub_source_false_gaps(body, pinned)
    assert "不输出日志" not in scrubbed
    assert "无日志输出" not in scrubbed
    assert "函数返回" not in scrubbed
    assert "保留这一行" in scrubbed


def test_vlog_source_truth_keeps_non_vasprintf_no_output_paths() -> None:
    pinned = "spdk_vlog vasprintf ext_buf"
    body = (
        "| level disabled | `severity < 0` 时 `spdk_vlog` 返回 | 不输出日志 |\n"
        "| vasprintf failure | `vasprintf` 返回 < 0 | 不输出日志 |\n"
        "| long message | `vasprintf` 堆分配 | 若分配失败则静默丢弃 |\n"
        "| error log | 若 `vasprintf` 失败则调用 `SPDK_ERRLOG` 并返回，不输出 |"
    )
    scrubbed = ReportGenerator._scrub_source_false_gaps(body, pinned)
    assert "severity < 0" in scrubbed
    assert "vasprintf failure" not in scrubbed
    assert "静默丢弃" not in scrubbed
    assert "SPDK_ERRLOG" not in scrubbed


def test_exact_source_evidence_summarizes_event_logflag_flow() -> None:
    card = EvidenceCard(
        card_id="event_app_impl",
        object_id="obj_event_app_logflag_cli",
        title="代码证据：app.c",
        source="repo_search",
        confidence="high",
        file_path=r"E:\repo\lib\event\app.c",
        symbol="option",
        snippet=(
            "110: #define LOGFLAG_OPT_IDX 'L'\n"
            "111: {\"logflag\", required_argument, NULL, LOGFLAG_OPT_IDX},\n"
            "1444: case LOGFLAG_OPT_IDX:\n"
            "1445:     rc = spdk_log_set_flag(optarg);\n"
            "1446:     if (rc < 0) {\n"
            "1447:         SPDK_ERRLOG(\"unknown flag: %s\\n\", optarg);\n"
        ),
    )

    exact = ReportGenerator._format_exact_source_evidence([card])

    assert "LOGFLAG_OPT_IDX" in exact
    assert "spdk_log_set_flag(optarg)" in exact
    assert "SPDK_ERRLOG" in exact
    assert "app_get_core_mask" not in exact


def test_event_logflag_fact_summary_reads_app_case_when_snippet_is_truncated(tmp_path) -> None:
    app_c = tmp_path / "lib" / "event" / "app.c"
    app_c.parent.mkdir(parents=True)
    app_c.write_text(
        "#define LOGFLAG_OPT_IDX 'L'\n"
        "static int parse(void) {\n"
        "case LOGFLAG_OPT_IDX:\n"
        "    rc = spdk_log_set_flag(optarg);\n"
        "    if (rc < 0) {\n"
        "        SPDK_ERRLOG(\"unknown flag: %s\\n\", optarg);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    card = EvidenceCard(
        card_id="event_app_impl",
        object_id="obj_event_app_logflag_cli",
        title="代码证据：app.c",
        source="repo_search",
        confidence="high",
        file_path=str(app_c),
        symbol="spdk_log_set_flag",
        snippet="#define LOGFLAG_OPT_IDX 'L'\n{\"logflag\", required_argument, NULL, LOGFLAG_OPT_IDX},",
    )

    exact = ReportGenerator._format_exact_source_evidence([card])

    assert "LOGFLAG_OPT_IDX" in exact
    assert "spdk_log_set_flag(optarg)" in exact
    assert "unknown flag" in exact


def test_event_logflag_scrub_removes_false_caller_error_gap() -> None:
    pinned = (
        "> Pinned exact source facts (deterministic, do not omit):\n"
        "> - `spdk_log_set_flag`: 源码定义 `LOGFLAG_OPT_IDX`/`--logflag` 命令行入口；"
        "`LOGFLAG_OPT_IDX` 分支调用 `spdk_log_set_flag(optarg)`；"
        "未知 log flag 通过 `SPDK_ERRLOG(\"unknown flag: %s\", optarg)` 报错并退出解析"
    )
    body = (
        "| **7. `--logflag` 处理中未知 flag 的错误处理是否完整？** | "
        "源码 `app.c` 中 `LOGFLAG_OPT_IDX` 分支调用 `spdk_log_set_flag(optarg)`，"
        "但 `app.c` 中未检查返回值，也未调用 `SPDK_ERRLOG`/`usage` 提示未知 flag。|\n"
        "| `spdk_log_set_flag` 中 `fnmatch` 匹配失败 | `log_set_flag` 返回 `-EINVAL`，"
        "无日志输出。 | 用户输入错误 flag 名称时无任何提示，可能导致用户误以为 flag 已启用。 |\n"
        "| 保留 | CLI 流程调用 `spdk_log_set_flag(optarg)` 并处理 `unknown flag`。 |"
    )

    scrubbed = ReportGenerator._scrub_source_false_gaps(body, pinned)

    assert "app.c` 中未检查返回值" not in scrubbed
    assert "未调用 `SPDK_ERRLOG`/`usage`" not in scrubbed
    assert "用户输入错误 flag 名称时无任何提示" not in scrubbed
    assert "CLI 调用方 `LOGFLAG_OPT_IDX` 分支负责 `SPDK_ERRLOG`/`usage` 用户提示" in scrubbed
    assert "保留" in scrubbed


def test_vlog_fast_path_malloc_coverage_is_downgraded_when_vasprintf_exists() -> None:
    pinned = (
        "> Pinned exact source facts (deterministic, do not omit):\n"
        "> - `spdk_vlog`: 源码在长消息分支调用 `vasprintf(&ext_buf, ...)`；"
        "源码在函数返回前调用 `free(ext_buf)` 释放堆缓冲"
    )
    body = (
        "| 设计章节 5 | 日志写入路径必须避免在 fast path 上调用 malloc，应使用静态缓冲或栈缓冲。 | "
        "是 (`lib/log/log.c`: `spdk_vlog` 使用栈缓冲 `_buf[MAX_TMPBUF]`，"
        "仅在超过大小时使用 `vasprintf` 分配堆内存) | 已覆盖 | 调用 `spdk_log` 或 `spdk_vlog` |\n"
        "| 保留 | `vasprintf(&ext_buf, ...)` 分支存在 | 待验证 |"
    )

    scrubbed = ReportGenerator._scrub_source_false_gaps(body, pinned)

    assert "| 部分覆盖 |" in scrubbed
    assert "| 已覆盖 |" not in scrubbed
    assert "fast path 定义" in scrubbed
    assert "保留" in scrubbed


def test_exact_source_evidence_summarizes_bdev_get_bdevs_rpc_flow() -> None:
    card = EvidenceCard(
        card_id="bdev_get_bdevs_impl",
        object_id="obj_bdev_get_bdevs_rpc",
        title="代码证据：bdev_rpc.c",
        source="repo_search",
        confidence="high",
        file_path=r"E:\repo\lib\bdev\bdev_rpc.c",
        symbol="rpc_bdev_get_bdevs",
        snippet=(
            '716: static const struct spdk_json_object_decoder rpc_bdev_get_bdevs_decoders[] = {\n'
            '717:     {"name", offsetof(struct rpc_bdev_get_bdevs_ctx, name), spdk_json_decode_string, true},\n'
            '718:     {"timeout", offsetof(struct rpc_bdev_get_bdevs_ctx, timeout), spdk_json_decode_uint64, true},\n'
            '722: rpc_bdev_get_bdev_cb(struct spdk_bdev_desc *desc, int rc, void *cb_arg)\n'
            '731:         rpc_dump_bdev_info(w, spdk_bdev_desc_get_bdev(desc));\n'
            '735:         spdk_bdev_close(desc);\n'
            '750: if (params && spdk_json_decode_object(params, rpc_bdev_get_bdevs_decoders,\n'
            '760: if (req.name) {\n'
            '764:     rc = spdk_bdev_open_async(req.name, false, dummy_bdev_event_cb, NULL, &opts,\n'
            '765:                               rpc_bdev_get_bdev_cb, request);\n'
            '780: spdk_for_each_bdev(w, rpc_dump_bdev_info);\n'
            '786: SPDK_RPC_REGISTER("bdev_get_bdevs", rpc_bdev_get_bdevs, SPDK_RPC_RUNTIME)\n'
        ),
    )

    exact = ReportGenerator._format_exact_source_evidence([card])

    assert 'SPDK_RPC_REGISTER("bdev_get_bdevs"' in exact
    assert "rpc_bdev_get_bdevs_decoders" in exact
    assert "spdk_bdev_open_async" in exact
    assert "rpc_bdev_get_bdev_cb" in exact
    assert "spdk_bdev_close(desc)" in exact
    assert "spdk_for_each_bdev" in exact


def test_pinned_source_facts_preserve_bdev_get_bdevs_terms() -> None:
    card = EvidenceCard(
        card_id="bdev_get_bdevs_impl",
        object_id="obj_bdev_get_bdevs_rpc",
        title="代码证据：bdev_rpc.c",
        source="repo_search",
        confidence="high",
        file_path=r"E:\repo\lib\bdev\bdev_rpc.c",
        symbol="rpc_bdev_get_bdevs",
        snippet=(
            'static const struct spdk_json_object_decoder rpc_bdev_get_bdevs_decoders[] = {\n'
            'SPDK_RPC_REGISTER("bdev_get_bdevs", rpc_bdev_get_bdevs, SPDK_RPC_RUNTIME)\n'
            'rc = spdk_bdev_open_async(req.name, false, dummy_bdev_event_cb, NULL, &opts,\n'
            '                          rpc_bdev_get_bdev_cb, request);\n'
            'spdk_bdev_close(desc);\n'
            'spdk_for_each_bdev(w, rpc_dump_bdev_info);\n'
        ),
    )

    pinned = ReportGenerator._format_pinned_source_facts([card])

    assert 'SPDK_RPC_REGISTER("bdev_get_bdevs"' in pinned
    assert "rpc_bdev_get_bdevs_decoders" in pinned
    assert "spdk_bdev_open_async" in pinned
    assert "spdk_for_each_bdev" in pinned


def test_bdev_fact_summary_reads_dump_info_when_snippet_is_truncated(tmp_path) -> None:
    bdev_rpc = tmp_path / "lib" / "bdev" / "bdev_rpc.c"
    bdev_rpc.parent.mkdir(parents=True)
    bdev_rpc.write_text(
        "static int rpc_dump_bdev_info(void *ctx, struct spdk_bdev *bdev) {\n"
        "    spdk_json_write_named_object_begin(w, \"supported_io_types\");\n"
        "    for (io_type = SPDK_BDEV_IO_TYPE_READ; io_type < SPDK_BDEV_NUM_IO_TYPES; ++io_type) {\n"
        "        name = spdk_bdev_get_io_type_name(io_type);\n"
        "        spdk_json_write_named_bool(w, name, spdk_bdev_io_type_supported(bdev, io_type));\n"
        "    }\n"
        "    spdk_json_write_object_end(w);\n"
        "}\n"
        "static void rpc_bdev_get_bdev_cb(struct spdk_bdev_desc *desc, int rc, void *cb_arg) {\n"
        "    rpc_dump_bdev_info(w, spdk_bdev_desc_get_bdev(desc));\n"
        "    spdk_bdev_close(desc);\n"
        "}\n"
        "SPDK_RPC_REGISTER(\"bdev_get_bdevs\", rpc_bdev_get_bdevs, SPDK_RPC_RUNTIME)\n",
        encoding="utf-8",
    )
    card = EvidenceCard(
        card_id="bdev_get_bdevs_impl",
        object_id="obj_bdev_get_bdevs_rpc",
        title="代码证据：bdev_rpc.c",
        source="repo_search",
        confidence="high",
        file_path=str(bdev_rpc),
        symbol="rpc_bdev_get_bdevs",
        snippet=(
            'rpc_dump_bdev_info(w, spdk_bdev_desc_get_bdev(desc));\n'
            'SPDK_RPC_REGISTER("bdev_get_bdevs", rpc_bdev_get_bdevs, SPDK_RPC_RUNTIME)\n'
        ),
    )

    pinned = ReportGenerator._format_pinned_source_facts([card])

    assert "supported_io_types" in pinned
    assert "spdk_bdev_io_type_supported" in pinned


def test_bdev_submit_fact_summary_reads_submit_windows_when_snippet_is_truncated(tmp_path) -> None:
    bdev_c = tmp_path / "lib" / "bdev" / "bdev.c"
    bdev_c.parent.mkdir(parents=True)
    bdev_c.write_text(
        "static inline void\n"
        "bdev_submit_request(struct spdk_bdev *bdev, struct spdk_io_channel *ioch,\n"
        "                    struct spdk_bdev_io *bdev_io)\n"
        "{\n"
        "    if ((bdev_io->type == SPDK_BDEV_IO_TYPE_WRITE ||\n"
        "         bdev_io->type == SPDK_BDEV_IO_TYPE_READ) &&\n"
        "        bdev_io->u.bdev.accel_sequence != NULL) {\n"
        "        bdev_io->internal.f.has_accel_sequence = false;\n"
        "    }\n"
        "    assert((bdev_io->u.bdev.dif_check_flags & bdev->dif_check_flags) ==\n"
        "           bdev_io->u.bdev.dif_check_flags);\n"
        "    bdev->fn_table->submit_request(ioch, bdev_io);\n"
        "}\n"
        "static inline void\n"
        "_bdev_io_submit(struct spdk_bdev_io *bdev_io)\n"
        "{\n"
        "    if (spdk_likely(bdev_ch->flags == 0)) {\n"
        "        bdev_io_do_submit(bdev_ch, bdev_io);\n"
        "        return;\n"
        "    }\n"
        "    if (bdev_ch->flags & BDEV_CH_RESET_IN_PROGRESS) {\n"
        "        _bdev_io_complete_in_submit(bdev_ch, bdev_io, SPDK_BDEV_IO_STATUS_ABORTED);\n"
        "    } else if (bdev_ch->flags & BDEV_CH_QOS_ENABLED) {\n"
        "        TAILQ_INSERT_TAIL(&bdev_ch->qos_queued_io, bdev_io, internal.link);\n"
        "        bdev_qos_io_submit(bdev_ch, bdev->internal.qos);\n"
        "    }\n"
        "}\n"
        "void\n"
        "bdev_io_submit(struct spdk_bdev_io *bdev_io)\n"
        "{\n"
        "    assert(bdev_io->internal.status == SPDK_BDEV_IO_STATUS_PENDING);\n"
        "    if (!bdev_io->internal.f.child_io && !TAILQ_EMPTY(&ch->locked_ranges)) {\n"
        "        TAILQ_INSERT_TAIL(&ch->io_locked, bdev_io, internal.ch_link);\n"
        "        return;\n"
        "    }\n"
        "    bdev_ch_add_to_io_submitted(bdev_io);\n"
        "    bdev_io->internal.submit_tsc = spdk_get_ticks();\n"
        "    spdk_trace_record_tsc(bdev_io->internal.submit_tsc, TRACE_BDEV_IO_START,\n"
        "                          ch->trace_id, bdev_io->u.bdev.num_blocks,\n"
        "                          (uintptr_t)bdev_io, (uint64_t)bdev_io->type,\n"
        "                          bdev_io->internal.caller_ctx,\n"
        "                          bdev_io->u.bdev.offset_blocks, ch->queue_depth);\n"
        "    if (bdev_io->internal.f.split) {\n"
        "        bdev_io_split(bdev_io);\n"
        "        return;\n"
        "    }\n"
        "    _bdev_io_submit(bdev_io);\n"
        "}\n"
        "int\n"
        "spdk_bdev_write(struct spdk_bdev_desc *desc, struct spdk_io_channel *ch,\n"
        "                void *buf, uint64_t offset, uint64_t nbytes,\n"
        "                spdk_bdev_io_completion_cb cb, void *cb_arg)\n"
        "{\n"
        "    if (bdev_bytes_to_blocks(desc, offset, &offset_blocks, nbytes, &num_blocks) != 0) {\n"
        "        return -EINVAL;\n"
        "    }\n"
        "    return spdk_bdev_write_blocks(desc, ch, buf, offset_blocks, num_blocks, cb, cb_arg);\n"
        "}\n"
        "static int\n"
        "bdev_write_blocks_with_md(struct spdk_bdev_desc *desc, struct spdk_io_channel *ch,\n"
        "                          void *buf, void *md_buf, uint64_t offset_blocks,\n"
        "                          uint64_t num_blocks, spdk_bdev_io_completion_cb cb,\n"
        "                          void *cb_arg)\n"
        "{\n"
        "    if (!desc->write) { return -EBADF; }\n"
        "    if (!bdev_io_valid_blocks(bdev, offset_blocks, num_blocks)) { return -EINVAL; }\n"
        "    bdev_io = bdev_channel_get_io(channel);\n"
        "    if (!bdev_io) { return -ENOMEM; }\n"
        "    bdev_io->type = SPDK_BDEV_IO_TYPE_WRITE;\n"
        "    bdev_io_submit(bdev_io);\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    card = EvidenceCard(
        card_id="bdev_submit_impl",
        object_id="obj_bdev_io_submit_path",
        title="代码证据：bdev.c",
        source="repo_search",
        confidence="high",
        file_path=str(bdev_c),
        symbol="bdev_io_submit",
        snippet="void bdev_io_submit(struct spdk_bdev_io *bdev_io) { _bdev_io_submit(bdev_io); }\n",
    )

    pinned = ReportGenerator._format_pinned_source_facts([card])

    assert "bdev_ch_add_to_io_submitted" in pinned
    assert "TRACE_BDEV_IO_START" in pinned
    assert "bdev_io_split" in pinned
    assert "bdev_io_do_submit" in pinned
    assert "BDEV_CH_QOS_ENABLED" in pinned
    assert "bdev_qos_io_submit" in pinned
    assert "bdev->fn_table->submit_request(ioch, bdev_io)" in pinned
    assert "SPDK_BDEV_IO_TYPE_WRITE" in pinned
    assert "-EBADF" in pinned


def test_bdev_submit_fact_summary_preserves_nomem_retry_state(tmp_path) -> None:
    bdev_c = tmp_path / "lib" / "bdev" / "bdev.c"
    bdev_c.parent.mkdir(parents=True)
    bdev_c.write_text(
        "enum bdev_io_retry_state {\n"
        "    BDEV_IO_RETRY_STATE_INVALID,\n"
        "    BDEV_IO_RETRY_STATE_SUBMIT,\n"
        "};\n"
        "static inline void\n"
        "bdev_queue_nomem_io_tail(struct spdk_bdev_shared_resource *shared_resource,\n"
        "                         struct spdk_bdev_io *bdev_io, enum bdev_io_retry_state state)\n"
        "{\n"
        "    assert(!TAILQ_EMPTY(&shared_resource->nomem_io));\n"
        "    bdev_io->internal.retry_state = state;\n"
        "    TAILQ_INSERT_TAIL(&shared_resource->nomem_io, bdev_io, internal.link);\n"
        "}\n"
        + ("/* padding to mimic the real bdev.c distance before _bdev_io_submit */\n" * 80) +
        "static inline void\n"
        "bdev_io_do_submit(struct spdk_bdev_channel *bdev_ch, struct spdk_bdev_io *bdev_io)\n"
        "{\n"
        "    if (spdk_likely(TAILQ_EMPTY(&shared_resource->nomem_io))) {\n"
        "        bdev_io_increment_outstanding(bdev_ch, shared_resource);\n"
        "        bdev_io->internal.f.in_submit_request = true;\n"
        "        bdev_submit_request(bdev, ch, bdev_io);\n"
        "        bdev_io->internal.f.in_submit_request = false;\n"
        "    } else {\n"
        "        bdev_queue_nomem_io_tail(shared_resource, bdev_io, BDEV_IO_RETRY_STATE_SUBMIT);\n"
        "        bdev_shared_ch_retry_io(shared_resource);\n"
        "    }\n"
        "}\n"
        + ("/* padding between submit helper and _bdev_io_submit */\n" * 80) +
        "static inline void\n"
        "_bdev_io_submit(struct spdk_bdev_io *bdev_io)\n"
        "{\n"
        "    bdev_io_do_submit(bdev_ch, bdev_io);\n"
        "}\n",
        encoding="utf-8",
    )
    card = EvidenceCard(
        card_id="bdev_submit_impl",
        object_id="obj_bdev_io_submit_path",
        title="代码证据：bdev.c",
        source="repo_search",
        confidence="high",
        file_path=str(bdev_c),
        symbol="bdev_io_submit",
        snippet="void bdev_io_submit(struct spdk_bdev_io *bdev_io) { _bdev_io_submit(bdev_io); }\n",
    )

    pinned = ReportGenerator._format_pinned_source_facts([card])

    assert "bdev_io_do_submit" in pinned
    assert "bdev_submit_request(bdev, ch, bdev_io)" in pinned
    assert "bdev_queue_nomem_io_tail" in pinned
    assert "BDEV_IO_RETRY_STATE_SUBMIT" in pinned


def test_bdev_gitnexus_source_cards_are_pinned_as_exact_facts(tmp_path) -> None:
    bdev_c = tmp_path / "lib" / "bdev" / "bdev.c"
    bdev_c.parent.mkdir(parents=True)
    bdev_c.write_text(
        "void\n"
        "bdev_io_submit(struct spdk_bdev_io *bdev_io)\n"
        "{\n"
        "    bdev_ch_add_to_io_submitted(bdev_io);\n"
        "    spdk_trace_record_tsc(tsc, TRACE_BDEV_IO_START, 0, 0, 0, 0, 0, 0, 0);\n"
        "    if (bdev_io->internal.f.split) { bdev_io_split(bdev_io); return; }\n"
        "    _bdev_io_submit(bdev_io);\n"
        "}\n"
        "static inline void\n"
        "_bdev_io_submit(struct spdk_bdev_io *bdev_io)\n"
        "{\n"
        "    if (bdev_ch->flags & BDEV_CH_QOS_ENABLED) {\n"
        "        bdev_qos_io_submit(bdev_ch, bdev->internal.qos);\n"
        "    } else {\n"
        "        bdev_io_do_submit(bdev_ch, bdev_io);\n"
        "    }\n"
        "}\n"
        "static inline void\n"
        "bdev_io_do_submit(struct spdk_bdev_channel *bdev_ch, struct spdk_bdev_io *bdev_io)\n"
        "{\n"
        "    if (TAILQ_EMPTY(&shared_resource->nomem_io)) {\n"
        "        bdev_submit_request(bdev, ch, bdev_io);\n"
        "    } else {\n"
        "        bdev_queue_nomem_io_tail(shared_resource, bdev_io, BDEV_IO_RETRY_STATE_SUBMIT);\n"
        "    }\n"
        "}\n"
        "static inline void\n"
        "bdev_submit_request(struct spdk_bdev *bdev, struct spdk_io_channel *ioch,\n"
        "                    struct spdk_bdev_io *bdev_io)\n"
        "{\n"
        "    bdev->fn_table->submit_request(ioch, bdev_io);\n"
        "}\n",
        encoding="utf-8",
    )
    card = EvidenceCard(
        card_id="gitnexus_bdev_submit_impl",
        object_id="obj_bdev_io_submit_path",
        title="代码证据：bdev.c",
        source="gitnexus",
        confidence="high",
        file_path=str(bdev_c),
        symbol="bdev_io_submit",
        snippet="void bdev_io_submit(struct spdk_bdev_io *bdev_io) { _bdev_io_submit(bdev_io); }\n",
    )

    pinned = ReportGenerator._format_pinned_source_facts([card])

    assert "bdev_ch_add_to_io_submitted" in pinned
    assert "TRACE_BDEV_IO_START" in pinned
    assert "bdev_io_do_submit" in pinned
    assert "bdev_queue_nomem_io_tail" in pinned
    assert "BDEV_IO_RETRY_STATE_SUBMIT" in pinned


def test_bdev_source_gap_scrub_removes_false_pending_rows() -> None:
    pinned = (
        "> Pinned exact source facts (deterministic, do not omit):\n"
        "> - `bdev_io_submit`: `bdev_io_submit` 会调用 "
        "`bdev_ch_add_to_io_submitted(bdev_io)` 并用 `TRACE_BDEV_IO_START`; "
        "`bdev_queue_nomem_io_tail(..., BDEV_IO_RETRY_STATE_SUBMIT)` exists."
    )
    body = (
        "| relation | evidence | status |\n"
        "| --- | --- | --- |\n"
        "| `bdev_io_submit` -> `TRACE_BDEV_IO_START` | 数据不足，请验证 | 待验证 |\n"
        "| `bdev_queue_nomem_io_tail` | 未在证据卡中命中 | 待验证 |\n"
        "\n"
        + pinned
    )

    scrubbed = ReportGenerator._scrub_source_false_gaps(body, pinned)

    assert "数据不足，请验证" not in scrubbed
    assert "未在证据卡中命中" not in scrubbed
    assert "bdev submit/read/write facts are backed" in scrubbed


def test_bdev_source_gap_scrub_removes_split_false_gap_context() -> None:
    body = (
        "### 待验证项\n\n"
        "以下用户附加说明中指定的符号在提供的证据卡片中**未找到实现源码**，标记为待验证：\n"
        "> Source fact guard: bdev submit/read/write facts are backed by "
        "`lib/bdev/bdev.c` source evidence and must not be reported as missing.\n"
        "- `bdev_io_submit(bdev_io)` — 已确认\n"
        "\n"
        "### 5. 跟踪点与 submitted 链表\n\n"
        "- `bdev_ch_add_to_io_submitted`：GitNexus 符号命中，但无源码实现\n"
        "- `TRACE_BDEV_IO_START`：未在任何证据卡中出现\n"
        "**问题**：无法确认 I/O 提交后的跟踪点插入位置和 submitted 链表管理逻辑。**待验证**。\n"
        "\n"
        "### 7. `bdev_queue_nomem_io_tail` 与 `BDEV_IO_RETRY_STATE_SUBMIT`\n\n"
        "- 这两个符号均未在任何证据卡中出现\n"
        "**问题**：内存不足时的 I/O 重试队列和重试状态管理逻辑完全缺失。**待验证**。\n"
        "\n"
        "### 8. `bdev_bytes_to_blocks` 辅助函数\n\n"
        "- 该函数未在任何证据卡中出现\n"
        "**问题**：无法确认字节到块转换的辅助函数是否存在及其实现。**待验证**。\n"
    )

    scrubbed = scrub_bdev_source_false_gaps(body)

    assert "未找到实现源码" not in scrubbed
    assert "无源码实现" not in scrubbed
    assert "未在任何证据卡中出现" not in scrubbed
    assert "无法确认 I/O 提交后的跟踪点" not in scrubbed
    assert "重试队列和重试状态管理逻辑完全缺失" not in scrubbed
    assert "辅助函数是否存在" not in scrubbed
    assert "bdev submit/read/write facts are backed" in scrubbed


def test_bdev_public_api_wrappers_are_preserved_as_source_facts(tmp_path) -> None:
    bdev_c = tmp_path / "lib" / "bdev" / "bdev.c"
    bdev_c.parent.mkdir(parents=True)
    bdev_c.write_text(
        "int\n"
        "spdk_bdev_read(struct spdk_bdev_desc *desc, struct spdk_io_channel *ch,\n"
        "               void *buf, uint64_t offset, uint64_t nbytes,\n"
        "               spdk_bdev_io_completion_cb cb, void *cb_arg)\n"
        "{\n"
        "    if (bdev_bytes_to_blocks(desc, offset, &offset_blocks, nbytes, &num_blocks) != 0) {\n"
        "        return -EINVAL;\n"
        "    }\n"
        "    return spdk_bdev_read_blocks(desc, ch, buf, offset_blocks, num_blocks, cb, cb_arg);\n"
        "}\n"
        "int\n"
        "spdk_bdev_read_blocks(struct spdk_bdev_desc *desc, struct spdk_io_channel *ch,\n"
        "                      void *buf, uint64_t offset_blocks, uint64_t num_blocks,\n"
        "                      spdk_bdev_io_completion_cb cb, void *cb_arg)\n"
        "{\n"
        "    return bdev_read_blocks_with_md(desc, ch, buf, NULL, offset_blocks, num_blocks, cb, cb_arg);\n"
        "}\n"
        "int\n"
        "spdk_bdev_write(struct spdk_bdev_desc *desc, struct spdk_io_channel *ch,\n"
        "                void *buf, uint64_t offset, uint64_t nbytes,\n"
        "                spdk_bdev_io_completion_cb cb, void *cb_arg)\n"
        "{\n"
        "    if (bdev_bytes_to_blocks(desc, offset, &offset_blocks, nbytes, &num_blocks) != 0) {\n"
        "        return -EINVAL;\n"
        "    }\n"
        "    return spdk_bdev_write_blocks(desc, ch, buf, offset_blocks, num_blocks, cb, cb_arg);\n"
        "}\n"
        "int\n"
        "spdk_bdev_write_blocks(struct spdk_bdev_desc *desc, struct spdk_io_channel *ch,\n"
        "                       void *buf, uint64_t offset_blocks, uint64_t num_blocks,\n"
        "                       spdk_bdev_io_completion_cb cb, void *cb_arg)\n"
        "{\n"
        "    return bdev_write_blocks_with_md(desc, ch, buf, NULL, offset_blocks, num_blocks, cb, cb_arg);\n"
        "}\n"
        "static int\n"
        "bdev_write_blocks_with_md(struct spdk_bdev_desc *desc, struct spdk_io_channel *ch,\n"
        "                          void *buf, void *md_buf, uint64_t offset_blocks,\n"
        "                          uint64_t num_blocks, spdk_bdev_io_completion_cb cb,\n"
        "                          void *cb_arg)\n"
        "{\n"
        "    if (!desc->write) { return -EBADF; }\n"
        "    bdev_io->type = SPDK_BDEV_IO_TYPE_WRITE;\n"
        "    bdev_io_submit(bdev_io);\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    card = EvidenceCard(
        card_id="bdev_public_api",
        object_id="obj_bdev_io_submit_path",
        title="代码证据：bdev.c",
        source="repo_search",
        confidence="high",
        file_path=str(bdev_c),
        symbol="spdk_bdev_write",
        snippet="int spdk_bdev_write(...) { return spdk_bdev_write_blocks(...); }\n",
    )

    pinned = ReportGenerator._format_pinned_source_facts([card])

    assert "spdk_bdev_read_blocks" in pinned
    assert "bdev_read_blocks_with_md" in pinned
    assert "spdk_bdev_write_blocks" in pinned
    assert "bdev_write_blocks_with_md" in pinned


def test_thread_poll_fact_summary_reads_poll_loop_when_snippet_is_truncated(tmp_path) -> None:
    thread_c = tmp_path / "lib" / "thread" / "thread.c"
    thread_c.parent.mkdir(parents=True)
    thread_c.write_text(
        "static inline uint32_t\n"
        "msg_queue_run_batch(struct spdk_thread *thread, uint32_t max_msgs)\n"
        "{\n"
        "    count = spdk_ring_dequeue(thread->messages, messages, max_msgs);\n"
        "    msg->fn(msg->arg);\n"
        "    SPIN_ASSERT(thread->lock_count == 0, SPIN_ERR_HOLD_DURING_SWITCH);\n"
        "    SLIST_INSERT_HEAD(&thread->msg_cache, msg, link);\n"
        "    spdk_mempool_put(g_spdk_msg_mempool, msg);\n"
        "}\n"
        "static int\n"
        "thread_poll(struct spdk_thread *thread, uint32_t max_msgs, uint64_t now)\n"
        "{\n"
        "    critical_msg = thread->critical_msg;\n"
        "    if (spdk_unlikely(critical_msg != NULL)) { critical_msg(NULL); }\n"
        "    msg_count = msg_queue_run_batch(thread, max_msgs);\n"
        "    TAILQ_FOREACH_REVERSE_SAFE(poller, &thread->active_pollers,\n"
        "                               active_pollers_head, tailq, tmp) {\n"
        "        poller_rc = thread_execute_poller(thread, poller);\n"
        "        if (thread->num_pp_handlers) { thread_run_pp_handlers(thread); }\n"
        "    }\n"
        "    poller = thread->first_timed_poller;\n"
        "    RB_REMOVE(timed_pollers_tree, &thread->timed_pollers, poller);\n"
        "    timer_rc = thread_execute_timed_poller(thread, poller, now);\n"
        "}\n"
        "int\n"
        "spdk_thread_poll(struct spdk_thread *thread, uint32_t max_msgs, uint64_t now)\n"
        "{\n"
        "    if (spdk_likely(!thread->in_interrupt)) {\n"
        "        rc = thread_poll(thread, max_msgs, now);\n"
        "    } else {\n"
        "        rc = spdk_fd_group_wait(thread->fgrp, 0);\n"
        "    }\n"
        "    thread_update_stats(thread, spdk_get_ticks(), now, rc);\n"
        "}\n",
        encoding="utf-8",
    )
    card = EvidenceCard(
        card_id="thread_poll_impl",
        object_id="obj_thread_poll_loop",
        title="代码证据：thread.c",
        source="repo_search",
        confidence="high",
        file_path=str(thread_c),
        symbol="spdk_thread_poll",
        snippet="int spdk_thread_poll(struct spdk_thread *thread, uint32_t max_msgs, uint64_t now) { return 0; }\n",
    )

    pinned = ReportGenerator._format_pinned_source_facts([card])

    assert "msg_queue_run_batch(thread, max_msgs)" in pinned
    assert "spdk_ring_dequeue" in pinned
    assert "msg->fn(msg->arg)" in pinned
    assert "SPIN_ASSERT(thread->lock_count == 0, SPIN_ERR_HOLD_DURING_SWITCH)" in pinned
    assert "SLIST_INSERT_HEAD(&thread->msg_cache, msg, link)" in pinned
    assert "spdk_mempool_put(g_spdk_msg_mempool, msg)" in pinned
    assert "critical_msg(NULL)" in pinned
    assert "TAILQ_FOREACH_REVERSE_SAFE(poller, &thread->active_pollers" in pinned
    assert "thread_execute_poller(thread, poller)" in pinned
    assert "thread_run_pp_handlers(thread)" in pinned
    assert "RB_REMOVE(timed_pollers_tree, &thread->timed_pollers, poller)" in pinned
    assert "thread_execute_timed_poller(thread, poller, now)" in pinned
    assert "spdk_fd_group_wait(thread->fgrp, 0)" in pinned
    assert "thread_update_stats(thread, spdk_get_ticks(), now, rc)" in pinned


def test_thread_send_msg_fact_summary_preserves_queueing_and_notification(tmp_path) -> None:
    thread_c = tmp_path / "lib" / "thread" / "thread.c"
    thread_c.parent.mkdir(parents=True)
    thread_c.write_text(
        "int\n"
        "spdk_thread_send_msg(const struct spdk_thread *thread, spdk_msg_fn fn, void *ctx)\n"
        "{\n"
        "    if (spdk_unlikely(thread->state == SPDK_THREAD_STATE_EXITED)) { abort(); }\n"
        "    if (local_thread->msg_cache_count > 0) {\n"
        "        msg = SLIST_FIRST(&local_thread->msg_cache);\n"
        "        SLIST_REMOVE_HEAD(&local_thread->msg_cache, link);\n"
        "    }\n"
        "    if (msg == NULL) { msg = spdk_mempool_get(g_spdk_msg_mempool); }\n"
        "    msg->fn = fn;\n"
        "    msg->arg = ctx;\n"
        "    rc = spdk_ring_enqueue(thread->messages, (void **)&msg, 1, NULL);\n"
        "    thread_send_msg_notification(thread);\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    card = EvidenceCard(
        card_id="thread_send_msg_impl",
        object_id="obj_spdk_thread_send_msg",
        title="代码证据：thread.c",
        source="repo_search",
        confidence="high",
        file_path=str(thread_c),
        symbol="spdk_thread_send_msg",
        snippet="int spdk_thread_send_msg(...) { thread_send_msg_notification(thread); }\n",
    )

    pinned = ReportGenerator._format_pinned_source_facts([card])

    assert "SPDK_THREAD_STATE_EXITED" in pinned
    assert "SLIST_FIRST(&local_thread->msg_cache)" in pinned
    assert "spdk_mempool_get(g_spdk_msg_mempool)" in pinned
    assert "spdk_ring_enqueue(thread->messages" in pinned
    assert "thread_send_msg_notification(thread)" in pinned


def test_thread_send_msg_fact_summary_marks_failure_paths_as_abort_not_errno(tmp_path) -> None:
    thread_c = tmp_path / "lib" / "thread" / "thread.c"
    thread_c.parent.mkdir(parents=True)
    thread_c.write_text(
        "int\n"
        "spdk_thread_send_msg(const struct spdk_thread *thread, spdk_msg_fn fn, void *ctx)\n"
        "{\n"
        "    if (spdk_unlikely(thread->state == SPDK_THREAD_STATE_EXITED)) {\n"
        "        SPDK_ERRLOG(\"Thread %s is marked as exited.\\n\", thread->name);\n"
        "        abort();\n"
        "    }\n"
        "    msg = spdk_mempool_get(g_spdk_msg_mempool);\n"
        "    if (!msg) {\n"
        "        SPDK_ERRLOG(\"msg could not be allocated\\n\");\n"
        "        abort();\n"
        "    }\n"
        "    rc = spdk_ring_enqueue(thread->messages, (void **)&msg, 1, NULL);\n"
        "    if (rc != 1) {\n"
        "        SPDK_ERRLOG(\"msg could not be enqueued\\n\");\n"
        "        abort();\n"
        "    }\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    card = EvidenceCard(
        card_id="thread_send_msg_abort_paths",
        object_id="obj_spdk_thread_send_msg",
        title="代码证据：thread.c",
        source="repo_search",
        confidence="high",
        file_path=str(thread_c),
        symbol="spdk_thread_send_msg",
        snippet="int spdk_thread_send_msg(...) { abort(); }\n",
    )

    pinned = ReportGenerator._format_pinned_source_facts([card])

    assert "do not report this as returning `-ENXIO`" in pinned
    assert "do not report this as returning `-ENOMEM`" in pinned
    assert "do not report this as returning `-ENOSPC`" in pinned


def test_poller_fact_summary_preserves_register_unregister_state_machine(tmp_path) -> None:
    thread_c = tmp_path / "lib" / "thread" / "thread.c"
    thread_c.parent.mkdir(parents=True)
    thread_c.write_text(
        "static struct spdk_poller *\n"
        "poller_register(spdk_poller_fn fn, void *arg, uint64_t period_microseconds, const char *name)\n"
        "{\n"
        "    poller = calloc(1, sizeof(*poller));\n"
        "    poller->state = SPDK_POLLER_STATE_WAITING;\n"
        "    poller->period_ticks = convert_us_to_ticks(period_microseconds);\n"
        "    thread_insert_poller(thread, poller);\n"
        "    return poller;\n"
        "}\n"
        "struct spdk_poller *spdk_poller_register_named(spdk_poller_fn fn, void *arg,\n"
        "        uint64_t period_microseconds, const char *name)\n"
        "{ return poller_register(fn, arg, period_microseconds, name); }\n"
        "void\n"
        "spdk_poller_unregister(struct spdk_poller **ppoller)\n"
        "{\n"
        "    if (!thread->poller_unregistered) {\n"
        "        thread->poller_unregistered = true;\n"
        "        spdk_thread_send_msg(thread, _thread_remove_pollers, thread);\n"
        "    }\n"
        "    if (poller->state == SPDK_POLLER_STATE_PAUSED) {\n"
        "        TAILQ_REMOVE(&thread->paused_pollers, poller, tailq);\n"
        "        TAILQ_INSERT_TAIL(&thread->active_pollers, poller, tailq);\n"
        "    }\n"
        "    poller->state = SPDK_POLLER_STATE_UNREGISTERED;\n"
        "}\n"
        "static void _thread_remove_pollers(void *ctx) {\n"
        "    if (poller->state == SPDK_POLLER_STATE_UNREGISTERED) { free(poller); }\n"
        "}\n",
        encoding="utf-8",
    )
    card = EvidenceCard(
        card_id="poller_state_machine",
        object_id="obj_poller_register_unregister",
        title="代码证据：thread.c",
        source="repo_search",
        confidence="high",
        file_path=str(thread_c),
        symbol="spdk_poller_unregister",
        snippet="void spdk_poller_unregister(struct spdk_poller **ppoller) { poller->state = SPDK_POLLER_STATE_UNREGISTERED; }\n",
    )

    pinned = ReportGenerator._format_pinned_source_facts([card])

    assert "poller_register" in pinned
    assert "SPDK_POLLER_STATE_WAITING" in pinned
    assert "thread_insert_poller(thread, poller)" in pinned
    assert "spdk_poller_register_named" in pinned
    assert "spdk_poller_unregister" in pinned
    assert "spdk_thread_send_msg(thread, _thread_remove_pollers, thread)" in pinned
    assert "SPDK_POLLER_STATE_UNREGISTERED" in pinned
    assert "_thread_remove_pollers" in pinned


def test_pinned_source_facts_preserve_event_logflag_terms_even_if_llm_omits_them(tmp_path) -> None:
    plan = build_default_plan(has_requirements=False, seed_examples=False)
    spec = ReportSpec(id="source_reading", title="source", template_id="source_reading")
    section = {"heading": "关键源码事实", "instructions": "summarize", "min_chars": 80}
    card = EvidenceCard(
        card_id="event_app_impl",
        object_id="obj_event_app_logflag_cli",
        title="代码证据：app.c",
        source="repo_search",
        confidence="high",
        file_path=r"E:\repo\lib\event\app.c",
        symbol="option",
        snippet=(
            "110: #define LOGFLAG_OPT_IDX 'L'\n"
            "111: {\"logflag\", required_argument, NULL, LOGFLAG_OPT_IDX},\n"
            "1444: case LOGFLAG_OPT_IDX:\n"
            "1445:     rc = spdk_log_set_flag(optarg);\n"
            "1446:     if (rc < 0) {\n"
            "1447:         SPDK_ERRLOG(\"unknown flag: %s\\n\", optarg);\n"
        ),
    )
    gen = ReportGenerator(_StaticLLM(), tmp_path, "task-pinned-facts")

    body, status = asyncio.run(
        gen._render_section(
            spec=spec,
            section=section,
            plan=plan,
            common_context={},
            analysis_units=[{"title": "event app logflag", "cards": [card]}],
            evidence_cards=[card],
            section_idx=0,
            sem=asyncio.Semaphore(1),
        )
    )

    assert status == "completed"
    assert "Pinned exact source facts" in body
    assert "LOGFLAG_OPT_IDX" in body
    assert "spdk_log_set_flag(optarg)" in body
    assert "unknown flag" in body


def test_render_section_uses_all_source_cards_for_requested_gap_detection(tmp_path) -> None:
    plan = build_default_plan(has_requirements=False, seed_examples=False)
    plan.user_guidance = (
        "Preserve exact source facts for `SPDK_THREAD_STATE_EXITED`, "
        "`spdk_mempool_get(g_spdk_msg_mempool)`, "
        "`spdk_ring_enqueue(thread->messages`, and "
        "`thread_send_msg_notification(thread)`."
    )
    plan.llm_limits.max_cards_per_report_section = 1
    spec = ReportSpec(id="source_reading", title="source", template_id="source_reading")
    section = {"heading": "关键源码事实", "instructions": "summarize", "min_chars": 80}

    thread_c = tmp_path / "lib" / "thread" / "thread.c"
    thread_c.parent.mkdir(parents=True)
    thread_c.write_text(
        "int\n"
        "spdk_thread_send_msg(const struct spdk_thread *thread, spdk_msg_fn fn, void *ctx)\n"
        "{\n"
        "    if (spdk_unlikely(thread->state == SPDK_THREAD_STATE_EXITED)) { abort(); }\n"
        "    msg = spdk_mempool_get(g_spdk_msg_mempool);\n"
        "    rc = spdk_ring_enqueue(thread->messages, (void **)&msg, 1, NULL);\n"
        "    thread_send_msg_notification(thread);\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    unrelated = EvidenceCard(
        card_id="unrelated",
        object_id="obj_unrelated",
        title="unrelated",
        source="repo_search",
        confidence="high",
        file_path=str(tmp_path / "other.c"),
        symbol="unrelated",
        snippet="int unrelated(void) { return 0; }\n",
    )
    thread_card = EvidenceCard(
        card_id="thread_send_msg_impl",
        object_id="obj_thread",
        title="代码证据：thread.c",
        source="repo_search",
        confidence="high",
        file_path=str(thread_c),
        symbol="spdk_thread_send_msg",
        snippet="int spdk_thread_send_msg(...) { thread_send_msg_notification(thread); }\n",
    )
    gen = ReportGenerator(_StaticLLM(), tmp_path, "task-thread-global-facts")

    body, status = asyncio.run(
        gen._render_section(
            spec=spec,
            section=section,
            plan=plan,
            common_context={},
            analysis_units=[{"title": "unrelated", "cards": [unrelated]}],
            evidence_cards=[unrelated, thread_card],
            section_idx=0,
            sem=asyncio.Semaphore(1),
        )
    )

    assert status == "completed"
    assert "Pinned exact source facts" in body
    assert "SPDK_THREAD_STATE_EXITED" in body
    assert "spdk_mempool_get(g_spdk_msg_mempool)" in body
    assert "spdk_ring_enqueue(thread->messages" in body
    assert "thread_send_msg_notification(thread)" in body
    assert "Requested identifiers without source evidence" not in body


def test_thread_source_truth_scrubs_false_incomplete_source_gaps() -> None:
    body = (
        "| item | reason |\n"
        "|---|---|\n"
        "| `spdk_thread_send_msg` 失败路径 `abort()` 实现 | 证据卡未提供完整源码，仅用户附加说明 |\n"
        "| `spdk_mempool_get` 失败时调用 `abort()` | 符号候选存在但源码未完整展示 |\n"
        "| `spdk_ring_enqueue` 失败时调用 `abort()` | 符号候选存在但源码未完整展示 |\n"
        "| `spdk_thread_send_msg` | 当前证据仅确认符号存在，未提供其实现源码，待源码验证 |\n"
        "`spdk_thread_send_msg`中`abort()`调用的具体条件需待源码验证（证据卡片中仅有符号存在，无具体实现）。\n"
        "### 2. `spdk_thread_poll` 消息分发与 poller 调度路径待验证\n"
        "**问题描述**：用户附加说明描述了完整调用链，但源码证据未提供 `spdk_thread_poll` 函数体。\n"
        "**待验证**：需确认 `spdk_thread_poll` 函数体是否包含上述完整调用链。\n"
        "### 3. unrelated heading\n"
        "此处保留。\n"
        "| 线程已退出 (`exited`) | `abort()` | 用户附加说明指定，待源码验证 |\n"
        "| `msg_queue_run_batch` | 待验证 | 仅符号命中，未获取完整函数体 |\n"
        "| unrelated | 待验证 |\n"
    )
    pinned = (
        "> Pinned exact source facts (deterministic, do not omit):\n"
        "> - `代码证据：thread.c`: `msg_queue_run_batch(thread, max_msgs)` uses "
        "`spdk_ring_dequeue`; `spdk_thread_poll` calls `thread_poll(thread, max_msgs, now)`; "
        "`thread_execute_poller(thread, poller)`; `spdk_fd_group_wait(thread->fgrp, 0)`; "
        "`spdk_thread_send_msg` when "
        "`spdk_mempool_get(g_spdk_msg_mempool)` returns NULL logs "
        "`msg could not be allocated` and calls `abort()`; do not report this as "
        "returning `-ENOMEM`"
    )

    scrubbed = ReportGenerator._scrub_thread_source_false_gaps(body, pinned)

    assert "证据卡未提供完整源码" not in scrubbed
    assert "源码未完整展示" not in scrubbed
    assert "未提供其实现源码" not in scrubbed
    assert "无具体实现" not in scrubbed
    assert "消息分发与 poller 调度路径待验证" not in scrubbed
    assert "未获取完整函数体" not in scrubbed
    assert "用户附加说明指定，待源码验证" not in scrubbed
    assert "此处保留" in scrubbed
    assert "unrelated" in scrubbed


def test_requested_identifier_gaps_preserve_missing_names_without_inventing_source() -> None:
    block = ReportGenerator._format_requested_identifier_gaps(
        "Verify `reset_log_deprecated`, `spdk_log_set_flag(optarg)`, and `unknown flag`.",
        "source evidence mentions spdk_log_set_flag only",
    )

    assert "reset_log_deprecated" in block
    assert "spdk_log_set_flag(optarg)" in block
    assert "unknown flag" in block
    assert "no selected source evidence was found" in block


def test_requested_identifier_gaps_compare_against_report_body_not_evidence() -> None:
    block = ReportGenerator._format_requested_identifier_gaps(
        "Preserve `spdk_log_set_flag(optarg)` and `unknown flag`.",
        "report body mentions only spdk_log_set_flag without exact argument",
    )

    assert "spdk_log_set_flag(optarg)" in block
    assert "unknown flag" in block


def test_requested_identifier_gaps_prioritize_round22_exact_terms() -> None:
    noisy_terms = " ".join(f"`noise_{i}`" for i in range(20))
    block = ReportGenerator._format_requested_identifier_gaps(
        noisy_terms + " `spdk_log_set_flag(optarg)` `unknown flag` `reset_log_deprecated`",
        "report body",
    )

    first_lines = "\n".join(block.splitlines()[:4])
    assert "spdk_log_set_flag(optarg)" in first_lines
    assert "unknown flag" in first_lines
    assert "reset_log_deprecated" in first_lines


def test_requested_identifier_gaps_find_round22_terms_without_backticks() -> None:
    block = ReportGenerator._format_requested_identifier_gaps(
        "final reports must preserve exact terms spdk_log_set_flag(optarg), unknown flag, and reset_log_deprecated",
        "report body",
    )

    assert "spdk_log_set_flag(optarg)" in block
    assert "unknown flag" in block
    assert "reset_log_deprecated" in block


def test_test_design_blueprint_does_not_assume_material_threshold_is_implemented() -> None:
    section = ReportGenerator._section_blueprints()["test_design"][3]

    assert "计数阈值" not in section["instructions"] or "1000" not in section["instructions"]
    assert "源码未实现" in section["instructions"]
    assert "需求未覆盖" in section["instructions"]


def test_section_budget_uses_generous_tokens_to_avoid_report_truncation(monkeypatch) -> None:
    monkeypatch.setattr("app.services.report_generator.settings.llm_max_output_tokens", 8192)
    assert ReportGenerator._section_budget_tokens(1200) >= 4800
    assert ReportGenerator._section_budget_tokens(3000) == 8192


def test_completed_with_warnings_message_distinguishes_degraded_from_failures() -> None:
    assert AnalysisPipeline._final_done_message(
        "completed_with_warnings",
        degraded=["cgc_unavailable", "gitnexus_repo_ambiguous"],
        all_modules_failed=False,
        no_reports=False,
    ) == "分析完成（存在降级警告）"

    assert AnalysisPipeline._final_done_message(
        "completed_with_warnings",
        degraded=[],
        all_modules_failed=True,
        no_reports=False,
    ) == "分析完成（部分内容生成失败）"


def test_requirements_prompt_distinguishes_implementation_from_requirement_coverage() -> None:
    section = ReportGenerator._section_blueprints()["requirements_traceability"][0]

    assert "实现存在不等于需求已覆盖" in section["instructions"]
    assert "requirement_status" in section["instructions"]


def test_exact_source_evidence_warns_against_neighbor_function_attribution() -> None:
    card = EvidenceCard(
        card_id="deprecated_impl",
        object_id="obj_deprecated",
        title="浠ｇ爜璇佹嵁锛歭og_deprecated.c",
        source="repo_search",
        confidence="high",
        file_path=r"E:\repo\lib\log\log_deprecated.c",
        symbol="spdk_log_deprecated",
        snippet=(
            "void\nspdk_log_deprecated(struct spdk_deprecation *dep, const char *file, uint32_t line, const char *func)\n"
            "{\n\tdep->hits++;\n}\n\nint\nspdk_log_for_each_deprecation(void *ctx, fn)\n{\n\tTAILQ_FOREACH(dep, &g_deprecations, link) {}\n}"
        ),
    )

    exact = ReportGenerator._format_exact_source_evidence([card])

    assert "不要把 `spdk_log_for_each_deprecation` 的 `TAILQ_FOREACH` 遍历归因到 `spdk_log_deprecated` 本体" in exact


def test_module_map_blueprint_keeps_neighbor_symbol_out_of_target_dependencies() -> None:
    section = ReportGenerator._section_blueprints()["module_map"][1]

    assert "owner_symbol" in section["instructions"]
    assert "owner_symbol" in section["instructions"]
    assert "evidence_symbol" in section["instructions"]
    assert "neighbor_context_only" in section["instructions"]


def test_plan_report_filenames_are_canonical_utf8_and_windows_safe() -> None:
    expected = {
        "project_structure": "10-项目结构初步理解.md",
        "module_map": "11-模块地图.md",
        "source_reading": "12-源码定向阅读记录.md",
        "business_flow": "13-关键业务流程分析.md",
        "gitnexus_reliability": "14-GitNexus结果可信度评估.md",
        "test_design": "15-测试视角代码理解.md",
        "requirements_traceability": "16-需求-设计-代码追踪.md",
    }
    illegal_windows_chars = set('<>:"/\\|?*')

    for template_id, filename in expected.items():
        spec = ReportSpec(id=template_id, title=template_id, template_id=template_id)
        actual = _plan_report_filename(spec)
        assert actual == filename
        assert not (set(actual) & illegal_windows_chars)
        assert "锛" not in actual
        assert "缁" not in actual
        assert "闇" not in actual


def test_custom_report_filename_slug_removes_windows_illegal_chars() -> None:
    spec = ReportSpec(
        id="custom_review",
        title='Custom: risk/report? "fast path" *check*',
        template_id="custom_review",
        custom=True,
    )

    filename = _plan_report_filename(spec)

    assert filename == "99-Custom_risk_report_fast_path_check.md"
    assert not (set(filename) & set('<>:"/\\|?*'))


def test_default_plan_report_titles_are_canonical_utf8() -> None:
    plan = build_default_plan(has_requirements=True, seed_examples=False)

    titles = {report.template_id: report.title for report in plan.reports}

    assert titles == {
        "project_structure": "项目结构初步理解",
        "module_map": "模块地图",
        "source_reading": "源码定向阅读记录",
        "business_flow": "关键业务流程分析",
        "gitnexus_reliability": "GitNexus 结果可信度评估",
        "test_design": "测试视角代码理解",
        "requirements_traceability": "需求-设计-代码追踪",
    }
    assert not any("锛" in title or "缁" in title or "闇" in title for title in titles.values())


def test_sfmea_validator_accepts_prompt_required_english_header() -> None:
    body = (
        "| Function/flow | Failure mode | Trigger | Injection point | Propagation | Impact | "
        "Observable signal | Severity | Probability | Detectability | Suggested test |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
        "| spdk_vlog | timestamp precision mismatch | log call | log.c:130 | bad timestamp | "
        "test fails | six digits | medium | high | high | assert nine digits |\n"
    )

    assert _section_is_invalid(body, min_chars=200, section={"requires_sfmea": True}) is False


class _StaticLLM(BaseLLMClient):
    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> LLMResponse:
        body = (
            "| Item | Evidence | Status |\n"
            "|---|---|---|\n"
            "| spdk_vlog | lib/log/log.c:130 uses CLOCK_REALTIME | 待源码验证 |\n\n"
            "```mermaid\nflowchart LR\nA[trigger] --> B[spdk_vlog]\n```\n\n"
            "| Function/flow | Failure mode | Trigger | Injection point | Propagation | Impact | "
            "Observable signal | Severity | Probability | Detectability | Suggested test |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|\n"
            "| spdk_vlog | timestamp precision mismatch | log call | log.c:130 | bad timestamp | "
            "test fails | six digits | medium | high | high | assert nine digits |\n\n"
            "This section is intentionally long enough to satisfy the report section validator "
            "while preserving source-first wording and explicit 待验证 markers."
        )
        return LLMResponse(content=body, model="static-test", usage={})

    async def health_check(self) -> tuple[bool, str]:
        return True, "ok"


class _ContradictoryOrchestrationLLM(_StaticLLM):
    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> LLMResponse:
        response = await super().complete(messages, max_tokens=max_tokens, temperature=temperature)
        body = (
            response.content
            + "\n\n- **编排顺序**：当前证据仅覆盖 log/thread 模块源码，"
            "未提供 GitNexus/CGC 图产品读取及低层 LLM 分析的编排代码。"
            "**数据不足，请验证** 编排顺序是否满足先读图产品再读源码。"
        )
        return LLMResponse(content=body, model="static-test", usage={})


class _ExtBufLeakLLM(_StaticLLM):
    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> LLMResponse:
        response = await super().complete(messages, max_tokens=max_tokens, temperature=temperature)
        body = (
            response.content
            + "\n\n| `spdk_vlog` allocation | `vasprintf(&ext_buf, ...)` | "
            "`free(ext_buf)` releases the heap buffer |\n"
            "| `spdk_vlog` – ext_buf leak | `vasprintf` succeeds but `ext_buf` not freed "
            "on all paths | Memory leak per call |\n"
        )
        return LLMResponse(content=body, model="static-test", usage={})


class _RecoveringTruncatedLLM(_StaticLLM):
    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> LLMResponse:
        self.calls += 1
        self.prompts.append(messages[-1]["content"])
        if self.calls == 1:
            current_finish_reason.set("length")
            note_truncation(current_task_id.get())
            return LLMResponse(
                content="| a | b |\n|---|---|\n| truncated | row |",
                model="static-test",
                usage={},
                truncated=True,
            )
        current_finish_reason.set("stop")
        return await super().complete(messages, max_tokens=max_tokens, temperature=temperature)


def test_generate_from_plan_writes_all_seven_reports_with_safe_names(tmp_path) -> None:
    plan = build_default_plan(has_requirements=True, seed_examples=False)
    gen = ReportGenerator(_StaticLLM(), tmp_path, "task-safe-filenames")

    manifest = asyncio.run(
        gen.generate_from_plan(
            plan=plan,
            scope_preview=None,
            analysis_units=[],
            evidence_cards=[],
            module_summaries=[],
            gitnexus_data={"nodes": [], "relationships": []},
            deepwiki_data={},
            requirements_doc="### req.md\nANCHOR-A runtime flag control",
            design_doc="### design.md\nANCHOR-B timestamp precision",
            pipeline_mode="gitnexus_only",
        )
    )

    assert len(manifest) == 7
    assert {entry["status"] for entry in manifest} == {"completed"}
    filenames = [entry["filename"] for entry in manifest]
    assert filenames == [
        "10-项目结构初步理解.md",
        "11-模块地图.md",
        "12-源码定向阅读记录.md",
        "13-关键业务流程分析.md",
        "14-GitNexus结果可信度评估.md",
        "15-测试视角代码理解.md",
        "16-需求-设计-代码追踪.md",
    ]
    for filename in filenames:
        path = tmp_path / filename
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "本报告由小段 LLM 调用组装而成" in text
        assert "锛" not in text
        assert "俓n" not in text


def test_generate_from_plan_forgives_provider_truncation_after_retry(tmp_path) -> None:
    task_id = "task-recovered-truncation"
    reset_truncation_count(task_id)
    token = current_task_id.set(task_id)
    try:
        plan = AnalysisPlan(
            reports=[
                ReportSpec(
                    id="source_reading",
                    title="源码定向阅读记录",
                    enabled=True,
                    template_id="source_reading",
                )
            ],
        )
        llm = _RecoveringTruncatedLLM()
        gen = ReportGenerator(llm, tmp_path, task_id)

        manifest = asyncio.run(
            gen.generate_from_plan(
                plan=plan,
                scope_preview=None,
                analysis_units=[],
                evidence_cards=[],
                module_summaries=[],
                gitnexus_data={"nodes": [], "relationships": []},
                deepwiki_data={},
                pipeline_mode="dual",
            )
        )
    finally:
        current_task_id.reset(token)

    assert manifest[0]["status"] == "completed"
    assert get_truncation_count(task_id) == 0
    assert any("Retry after provider truncation" in prompt for prompt in llm.prompts[1:])
    manifest_json = json.loads((tmp_path / "report_manifest.json").read_text(encoding="utf-8"))
    assert manifest_json["degraded"] == []
    assert manifest_json["llm_truncations"] == 0


def test_generate_from_plan_canonicalizes_mojibake_report_titles(tmp_path) -> None:
    plan = build_default_plan(has_requirements=True, seed_examples=False)
    mojibake_titles = {
        "project_structure": "é¡¹ç®ç»æåæ­¥çè§£",
        "module_map": "æ¨¡åå°å¾",
        "source_reading": "æºç å®åéè¯»è®°å½",
        "business_flow": "å³é®ä¸å¡æµç¨åæ",
        "gitnexus_reliability": "GitNexus ç»æå¯ä¿¡åº¦è¯ä¼°",
        "test_design": "æµè¯è§è§ä»£ç çè§£",
        "requirements_traceability": "é€æ±-è®¾è®¡-ä»£ç è¿½è¸ª",
    }
    for report in plan.reports:
        report.title = mojibake_titles[report.template_id]
    gen = ReportGenerator(_StaticLLM(), tmp_path, "task-title-canonical")

    manifest = asyncio.run(
        gen.generate_from_plan(
            plan=plan,
            scope_preview=None,
            analysis_units=[],
            evidence_cards=[],
            module_summaries=[],
            gitnexus_data={"nodes": [], "relationships": []},
            deepwiki_data={},
            pipeline_mode="gitnexus_only",
        )
    )

    titles = [entry["title"] for entry in manifest]
    assert titles == [
        "项目结构初步理解",
        "模块地图",
        "源码定向阅读记录",
        "关键业务流程分析",
        "GitNexus 结果可信度评估",
        "测试视角代码理解",
        "需求-设计-代码追踪",
    ]
    manifest_json = json.loads((tmp_path / "report_manifest.json").read_text(encoding="utf-8"))
    assert [entry["title"] for entry in manifest_json["reports"]] == titles
    first_report = (tmp_path / "10-项目结构初步理解.md").read_text(encoding="utf-8")
    assert "# 项目结构初步理解" in first_report
    assert "é¡¹" not in first_report


def test_generate_from_plan_injects_tool_orchestration_into_non_structure_reports(tmp_path) -> None:
    plan = AnalysisPlan(
        analysis_objects=[
            AnalysisObject(
                id="obj_log",
                text="SPDK log/vlog path",
                kind="topic",
                priority="high",
                path_hints=["lib/log/log.c", "include/spdk/log.h"],
            )
        ],
        reports=[
            ReportSpec(
                id="source_reading",
                title="Source Reading Regression",
                enabled=True,
                template_id="source_reading",
            )
        ],
    )
    gen = ReportGenerator(_StaticLLM(), tmp_path, "task-tool-orchestration")

    asyncio.run(
        gen.generate_from_plan(
            plan=plan,
            scope_preview=None,
            analysis_units=[],
            evidence_cards=[],
            module_summaries=[],
            gitnexus_data={"nodes": [], "relationships": []},
            deepwiki_data={},
            pipeline_mode="dual",
            index_coverage={
                "agent_cwd": r"E:\codetalk_test\codetalks-Test\codetalk",
                "target_path": _TARGET,
                "gitnexus_index_root": "spdk",
                "gitnexus_index_path": _TARGET,
                "gitnexus_stats": {
                    "actual": {"nodes": 50401, "edges": 101699},
                    "expected": {"nodes": 50401, "edges": 101699},
                    "matched": True,
                },
                "cgc_index_root": [
                    r"E:\codetalk_test\codetalks-Test\fixtures\spdk\include\spdk",
                    r"E:\codetalk_test\codetalks-Test\fixtures\spdk\lib\log",
                ],
            },
        )
    )

    text = (tmp_path / "12-源码定向阅读记录.md").read_text(encoding="utf-8")
    assert "Tool Orchestration" in text
    assert "CGC INDEX_ROOT" in text
    assert "GitNexus graph stats" in text
    assert "50401 nodes / 101699 edges" in text
    assert "products -> source reads -> LLM" in text


def test_generate_from_plan_scrubs_false_missing_tool_orchestration_claim(tmp_path) -> None:
    plan = AnalysisPlan(
        analysis_objects=[
            AnalysisObject(id="obj_log", text="SPDK log/vlog path", kind="topic")
        ],
        reports=[
            ReportSpec(
                id="source_reading",
                title="Source Reading Regression",
                enabled=True,
                template_id="source_reading",
            )
        ],
    )
    gen = ReportGenerator(_ContradictoryOrchestrationLLM(), tmp_path, "task-tool-scrub")

    asyncio.run(
        gen.generate_from_plan(
            plan=plan,
            scope_preview=None,
            analysis_units=[],
            evidence_cards=[],
            module_summaries=[],
            gitnexus_data={"nodes": [], "relationships": []},
            deepwiki_data={},
            pipeline_mode="dual",
            index_coverage={
                "target_path": _TARGET,
                "gitnexus_index_path": _TARGET,
                "gitnexus_stats": {
                    "actual": {"nodes": 50401, "edges": 101699},
                    "matched": True,
                },
                "cgc_index_root": [r"E:\codetalk_test\codetalks-Test\fixtures\spdk\lib\log"],
            },
        )
    )

    text = (tmp_path / "12-源码定向阅读记录.md").read_text(encoding="utf-8")
    assert "Tool Orchestration" in text
    assert "未提供 GitNexus/CGC 图产品读取" not in text
    assert "数据不足，请验证" not in text


def test_generate_from_plan_scrubs_false_ext_buf_leak_claim(tmp_path) -> None:
    plan = AnalysisPlan(
        analysis_objects=[
            AnalysisObject(id="obj_log", text="SPDK log/vlog path", kind="topic")
        ],
        reports=[
            ReportSpec(
                id="test_design",
                title="Test Design Regression",
                enabled=True,
                template_id="test_design",
            )
        ],
    )
    gen = ReportGenerator(_ExtBufLeakLLM(), tmp_path, "task-ext-buf-scrub")

    asyncio.run(
        gen.generate_from_plan(
            plan=plan,
            scope_preview=None,
            analysis_units=[],
            evidence_cards=[],
            module_summaries=[],
            gitnexus_data={"nodes": [], "relationships": []},
            deepwiki_data={},
            pipeline_mode="dual",
            index_coverage={
                "target_path": _TARGET,
                "gitnexus_index_path": _TARGET,
                "gitnexus_stats": {
                    "actual": {"nodes": 50401, "edges": 101699},
                    "matched": True,
                },
                "cgc_index_root": [r"E:\codetalk_test\codetalks-Test\fixtures\spdk\lib\log"],
            },
        )
    )

    text = (tmp_path / "15-测试视角代码理解.md").read_text(encoding="utf-8")
    assert "free(ext_buf)" in text
    assert "ext_buf leak" not in text
    assert "not freed" not in text
    assert "Memory leak per call" not in text


def test_analysis_unit_mapping_covers_all_planned_objects() -> None:
    plan = AnalysisPlan(
        analysis_objects=[
            AnalysisObject(id="obj_vlog", text="spdk_vlog", kind="function"),
            AnalysisObject(id="obj_set", text="spdk_log_set_flag", kind="function"),
            AnalysisObject(id="obj_missing", text="spdk_log_find_flag", kind="function"),
        ],
        reports=[],
    )
    resolved = [
        ResolvedAnalysisObject(
            object_id="obj_vlog",
            text="spdk_vlog",
            candidate_symbols=[
                ScopeCandidate(
                    path="lib/log/log.c",
                    symbol="spdk_vlog",
                    source="gitnexus",
                    confidence="high",
                    reason="symbol hit",
                )
            ],
        ),
        ResolvedAnalysisObject(
            object_id="obj_set",
            text="spdk_log_set_flag",
            candidate_symbols=[
                ScopeCandidate(
                    path="lib/log/log_flags.c",
                    symbol="spdk_log_set_flag",
                    source="gitnexus",
                    confidence="high",
                    reason="symbol hit",
                )
            ],
        ),
        ResolvedAnalysisObject(
            object_id="obj_missing",
            text="spdk_log_find_flag",
            warnings=["not found"],
        ),
    ]
    cards = [
        EvidenceCard(
            card_id="card_vlog",
            object_id="obj_vlog",
            title="vlog",
            source="repo_search",
            confidence="high",
            file_path="lib/log/log.c",
            symbol="spdk_vlog",
        ),
        EvidenceCard(
            card_id="card_set",
            object_id="obj_set",
            title="set",
            source="repo_search",
            confidence="high",
            file_path="lib/log/log_flags.c",
            symbol="spdk_log_set_flag",
        ),
    ]
    pipe = AnalysisPipeline()
    pipe._repo_path = r"E:\repo"
    pipe._evidence_cards = cards
    units = pipe._group_analysis_units(resolved, plan)

    mapping = pipe._build_analysis_unit_mapping(resolved, plan, units)

    assert mapping["plan_object_count"] == 3
    by_id = {item["object_id"]: item for item in mapping["objects"]}
    assert by_id["obj_vlog"]["unit_id"] == "unit_1"
    assert by_id["obj_vlog"]["evidence_card_ids"] == ["card_vlog"]
    assert by_id["obj_set"]["unit_id"] == "unit_2"
    assert by_id["obj_missing"]["coverage_status"] == "unresolved"
    assert by_id["obj_missing"]["warnings"] == ["not found"]
    assert {unit["unit_id"] for unit in mapping["units"]} == {"unit_1", "unit_2", "unit_3"}


def test_main_app_mounts_task_logs_websocket_route() -> None:
    from app.main import app

    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/ws/tasks/{task_id}/logs" in paths


@pytest.mark.asyncio
async def test_cgc_prepare_failure_does_not_leave_unretrieved_future() -> None:
    adapter = CGCAdapter(base_url="http://cgc-unavailable:7072")

    async def _fail_index(path: str):
        raise RuntimeError("cgc down")

    adapter._cgc.index_repo = _fail_index
    request = AnalysisRequest(repo_local_path=r"E:\repo")
    loop = asyncio.get_running_loop()
    events: list[dict] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: events.append(context))
    try:
        with pytest.raises(RuntimeError, match="cgc down"):
            await adapter.prepare(request)

        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)
        CGCAdapter._prepare_inflight.clear()

    assert not any(
        context.get("message") == "Future exception was never retrieved"
        for context in events
    )


# --- _fetch_gitnexus_graph disambiguation ---------------------------------


class _Resp:
    def __init__(self, status: int, payload: dict) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _graph(n_nodes: int, n_edges: int) -> dict:
    return {"nodes": [0] * n_nodes, "relationships": [0] * n_edges}


def _graph_edges(n_nodes: int, n_edges: int) -> dict:
    return {"nodes": [0] * n_nodes, "edges": [0] * n_edges}


class _FakeClient:
    """Returns the target graph only when a disambiguating `path` is supplied."""

    def __init__(self, *, honor_path: bool) -> None:
        self.honor_path = honor_path
        self.calls: list[dict] = []

    async def get(self, url, params=None, timeout=None):
        params = dict(params or {})
        self.calls.append(params)
        if self.honor_path and params.get("path") == _TARGET:
            return _Resp(200, _graph(50401, 101699))  # correct repo
        return _Resp(200, _graph(50259, 101329))      # wrong same-named repo


class _FakeEdgesClient(_FakeClient):
    """Mirrors the real GitNexus API shape: graph edges are returned as `edges`."""

    async def get(self, url, params=None, timeout=None):
        params = dict(params or {})
        self.calls.append(params)
        if self.honor_path and params.get("path") == _TARGET:
            return _Resp(200, _graph_edges(50401, 101699))
        return _Resp(200, _graph_edges(50259, 101329))


@pytest.fixture(autouse=True)
def _silence_log_step(monkeypatch):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(AnalysisPipeline, "_log_step", staticmethod(_noop))


def test_fetch_graph_disambiguates_by_path() -> None:
    pipe = AnalysisPipeline()
    descriptor = resolve_indexed_repo(_DUP_PAYLOAD, _TARGET)
    client = _FakeClient(honor_path=True)
    graph = asyncio.run(pipe._fetch_gitnexus_graph(client, "spdk", descriptor))
    assert len(graph["nodes"]) == 50401  # picked the correct repo
    assert pipe._gitnexus_stats["matched"] is True
    assert "gitnexus_repo_ambiguous" not in pipe._gitnexus_extra_degraded


def test_fetch_graph_accepts_edges_field_from_real_gitnexus() -> None:
    pipe = AnalysisPipeline()
    descriptor = resolve_indexed_repo(_DUP_PAYLOAD, _TARGET)
    client = _FakeEdgesClient(honor_path=True)
    graph = asyncio.run(pipe._fetch_gitnexus_graph(client, "spdk", descriptor))
    assert len(graph["nodes"]) == 50401
    assert len(graph["edges"]) == 101699
    assert pipe._gitnexus_stats["actual"] == {"nodes": 50401, "edges": 101699}
    assert pipe._gitnexus_stats["matched"] is True
    assert "gitnexus_repo_ambiguous" not in pipe._gitnexus_extra_degraded


def test_fetch_graph_flags_degraded_when_params_ignored() -> None:
    pipe = AnalysisPipeline()
    descriptor = resolve_indexed_repo(_DUP_PAYLOAD, _TARGET)
    client = _FakeClient(honor_path=False)  # GitNexus ignores path -> always wrong
    graph = asyncio.run(pipe._fetch_gitnexus_graph(client, "spdk", descriptor))
    assert len(graph["nodes"]) == 50259  # could not get the right one
    assert pipe._gitnexus_stats["matched"] is False
    assert "gitnexus_repo_ambiguous" in pipe._gitnexus_extra_degraded


def test_fetch_graph_single_repo_no_extra_calls() -> None:
    pipe = AnalysisPipeline()
    # Non-ambiguous descriptor -> only the plain {"repo": name} query is used.
    descriptor = {"name": "spdk", "path": _TARGET, "ambiguous": False,
                  "node_count": None, "edge_count": None}
    client = _FakeClient(honor_path=True)
    asyncio.run(pipe._fetch_gitnexus_graph(client, "spdk", descriptor))
    assert client.calls == [{"repo": "spdk"}]


def test_index_coverage_table_shows_path_and_stats_mismatch() -> None:
    ctx = {
        "pipeline_mode": "gitnexus_only",
        "active_materials": [],
        "index_coverage": {
            "agent_cwd": "/work",
            "target_path": _TARGET,
            "gitnexus_index_root": "spdk",
            "gitnexus_index_path": _TARGET,
            "gitnexus_stats": {
                "expected": {"nodes": 50401, "edges": 101699},
                "actual": {"nodes": 50259, "edges": 101329},
                "matched": False,
            },
            "cgc_index_root": "unavailable",
        },
    }
    section = build_index_coverage_section(ctx)
    assert "INDEX_PATH" in section
    assert "50401" in section and "50259" in section
    assert "gitnexus_repo_ambiguous" in section
