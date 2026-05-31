"""Regression tests for the 2026-05-30/31 Round 2/3 E2E buglist fixes.

Deterministic, tool-independent coverage of:

* GitNexus /api/repos shape tolerance + path-based disambiguation of duplicate
  repo names (Round 2 #1 / Round 3 #4)
* LLM-truncation tally surfaced for run health (Round 2 #3 / Round 3 #6)
* duplicate section-heading stripping (Round 3 quality)
* codetalks `00` index-coverage section (Round 3 Next Action #1)
"""

from app.adapters.gitnexus import (
    _entry_paths,
    _path_matches,
    resolve_indexed_repo_name,
)
from app.schemas.workspace_analysis import build_default_plan
from app.llm.base import (
    forgive_truncation,
    get_truncation_count,
    note_truncation,
    reset_truncation_count,
)
from app.services.report_generator import (
    build_index_coverage_section,
    strip_duplicate_heading,
)


# ---------------------------------------------------------------------------
# GitNexus repo resolution (the core Round 2/3 orchestration bug)
# ---------------------------------------------------------------------------


def test_resolve_top_level_array_with_duplicate_names() -> None:
    # Real service shape: bare top-level list, two repos both named "spdk".
    payload = [
        {"name": "spdk", "path": r"D:\coworkers\spdk"},
        {"name": "spdk", "path": r"E:\codetalk_test\codetalks-Test\fixtures\spdk"},
    ]
    target = r"E:\codetalk_test\codetalks-Test\fixtures\spdk"
    assert resolve_indexed_repo_name(payload, target) == "spdk"


def test_resolve_does_not_match_wrong_duplicate() -> None:
    # When the target path is NOT among indexed paths, the duplicate name must
    # not produce a false positive -> returns None -> caller re-indexes safely.
    payload = [
        {"name": "spdk", "path": r"D:\coworkers\spdk"},
        {"name": "spdk", "path": r"C:\other\spdk"},
    ]
    target = r"E:\codetalk_test\codetalks-Test\fixtures\spdk"
    assert resolve_indexed_repo_name(payload, target) is None


def test_resolve_object_wrapper_shape() -> None:
    payload = {"repos": [{"repo": "myproj", "root": "/srv/repos/myproj"}]}
    assert resolve_indexed_repo_name(payload, "/srv/repos/myproj") == "myproj"


def test_resolve_string_entries_unique_basename() -> None:
    payload = ["/srv/repos/alpha", "/srv/repos/beta"]
    assert resolve_indexed_repo_name(payload, "/somewhere/else/beta") == "beta"


def test_path_matches_prefix_tolerance() -> None:
    # container vs host prefix difference, same basename -> match
    assert _path_matches("/host/a/b/spdk", "/b/spdk") is True
    # different distinct repos same basename -> no match
    assert _path_matches(r"E:\x\spdk", r"D:\y\spdk") is False


def test_entry_paths_collects_path_like_values() -> None:
    paths = _entry_paths({"name": "spdk", "indexRoot": r"E:\x\spdk"})
    assert r"E:\x\spdk" in paths


# ---------------------------------------------------------------------------
# Truncation tally
# ---------------------------------------------------------------------------


def test_truncation_counter_lifecycle() -> None:
    tid = "task-xyz"
    reset_truncation_count(tid)
    assert get_truncation_count(tid) == 0
    note_truncation(tid)
    note_truncation(tid)
    assert get_truncation_count(tid) == 2
    forgive_truncation(tid)
    assert get_truncation_count(tid) == 1
    forgive_truncation(tid, 10)
    assert get_truncation_count(tid) == 0
    reset_truncation_count(tid)
    assert get_truncation_count(tid) == 0
    # None task id is a no-op, not an error
    note_truncation(None)
    forgive_truncation(None)
    assert get_truncation_count(None) == 0


# ---------------------------------------------------------------------------
# Duplicate heading stripping
# ---------------------------------------------------------------------------


def test_strip_duplicate_heading_removes_repeat() -> None:
    assert strip_duplicate_heading("## 测试建议\n正文内容", "测试建议") == "正文内容"
    assert strip_duplicate_heading("### 状态类测试点\n表格", "状态类测试点") == "表格"


def test_strip_duplicate_heading_keeps_distinct_heading() -> None:
    body = "## 另一个小节\n内容"
    assert strip_duplicate_heading(body, "测试建议") == body


# ---------------------------------------------------------------------------
# 00 index-coverage section
# ---------------------------------------------------------------------------


def test_requirements_traceability_enabled_with_materials() -> None:
    """LegacyIssues handoff: with active requirements/design material, the
    traceability report defaults ON (7 reports); without, it isn't added."""
    with_reqs = build_default_plan(has_requirements=True)
    rt = [r for r in with_reqs.reports if r.template_id == "requirements_traceability"]
    assert len(rt) == 1 and rt[0].enabled is True
    assert len(with_reqs.enabled_reports()) == 7

    without = build_default_plan(has_requirements=False)
    assert not any(
        r.template_id == "requirements_traceability" for r in without.reports
    )
    assert len(without.enabled_reports()) == 6


def test_index_coverage_section_has_required_tokens() -> None:
    ctx = {
        "pipeline_mode": "gitnexus_only",
        "active_materials": ["design_doc_80KB.md"],
        "index_coverage": {
            "agent_cwd": "/work",
            "target_path": "/repo/spdk",
            "gitnexus_index_root": "spdk",
            "cgc_index_root": "不可用",
        },
    }
    section = build_index_coverage_section(ctx)
    for token in ("AGENT_CWD", "TARGET_PATH", "INDEX_ROOT", "00 本轮依据与索引覆盖范围"):
        assert token in section, token
    # degraded mode wording surfaces
    assert "cgc_unavailable" in section
