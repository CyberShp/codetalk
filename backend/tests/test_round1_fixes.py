"""Regression tests for the 2026-05-30 Round 1 E2E handoff fixes.

These cover the deterministic, tool-independent halves of each fix so they run
without GitNexus / CGC / an LLM:

* #1 preview ↔ execution analysis-unit planner consistency
* #3 material coverage (multi-material + late anchors survive truncation)
* #4 truncation detection (section never marked completed when cut off)
* #5 report postprocess (LLM 客套 openings stripped)
"""

from app.services.report_generator import (
    _section_is_invalid,
    build_material_excerpt,
    looks_truncated,
    strip_llm_preamble,
)
from app.services.workspace_scope_resolver import plan_analysis_units


# ---------------------------------------------------------------------------
# #1 planner consistency
# ---------------------------------------------------------------------------


def test_objects_sharing_a_file_collapse_to_one_unit() -> None:
    object_files = [
        ("a", ["/r/lib/log/log.c"]),
        ("b", ["/r/lib/log/log.c", "/r/lib/log/x.c"]),
        ("c", ["/r/lib/log/log.c"]),
    ]
    groups = plan_analysis_units(object_files, max_units=12)
    assert len(groups) == 1


def test_disjoint_objects_stay_separate() -> None:
    object_files = [("a", ["/r/x.c"]), ("b", ["/r/y.c"]), ("c", ["/r/z.c"])]
    assert len(plan_analysis_units(object_files, max_units=12)) == 3


def test_unit_cap_merges_tail() -> None:
    object_files = [("a", ["/r/x.c"]), ("b", ["/r/y.c"]), ("c", ["/r/z.c"])]
    groups = plan_analysis_units(object_files, max_units=2)
    assert len(groups) == 2
    # the last bucket absorbs the merged tail
    assert {"b", "c"}.issubset(set(groups[-1])) or set(groups[-1]) == {"c", "b"}


# ---------------------------------------------------------------------------
# #3 material coverage
# ---------------------------------------------------------------------------


def test_both_materials_and_late_anchors_survive() -> None:
    doc = (
        "### design_doc_80KB.md\n"
        + ("x" * 40000)
        + "\nANCHOR-C constraint\nANCHOR-D rule\n"
        + "\n\n### design_doc_20KB.md\n"
        + ("y" * 5000)
        + "\nANCHOR-E final"
    )
    excerpt, names = build_material_excerpt(doc, 30000)
    assert names == ["design_doc_80KB.md", "design_doc_20KB.md"]
    # the second material is not dropped
    assert "design_doc_20KB.md" in excerpt
    # late anchors buried past the truncation point still appear
    for anchor in ("ANCHOR-C", "ANCHOR-D", "ANCHOR-E"):
        assert anchor in excerpt, anchor


def test_empty_material_returns_nothing() -> None:
    assert build_material_excerpt(None, 30000) == ("", [])


# ---------------------------------------------------------------------------
# #4 truncation detection
# ---------------------------------------------------------------------------


def test_looks_truncated_flags() -> None:
    assert looks_truncated("表格\n| a | b") is True  # unterminated row
    assert looks_truncated("列表项 **2.") is True  # dangling enumerator
    assert looks_truncated("代码:\n```python\nfoo()") is True  # open fence
    assert looks_truncated("分析结束。") is False


def test_truncated_section_is_invalid() -> None:
    body = "## 调用链\n" + "正常内容 " * 20 + "\n| 列1 | 列2"
    assert _section_is_invalid(body, min_chars=10, section={}) is True


# ---------------------------------------------------------------------------
# #5 postprocess
# ---------------------------------------------------------------------------


def test_strip_preamble_removes_kuhao() -> None:
    assert strip_llm_preamble("好的，作为代码专家。\n实际内容") == "实际内容"
    assert strip_llm_preamble("下面是模块地图：\n## 模块") == "## 模块"
    assert strip_llm_preamble("我将基于证据卡进行分析。\n正文") == "正文"


def test_strip_preamble_leaves_real_content() -> None:
    assert strip_llm_preamble("## 正文标题") == "## 正文标题"
    assert strip_llm_preamble("模块地图如下") == "模块地图如下"
