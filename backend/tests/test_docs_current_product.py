from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_public_docs_match_current_navigation_and_ports():
    docs = "\n".join(
        (ROOT / rel).read_text(encoding="utf-8")
        for rel in ("README.md", "docs/USER_MANUAL.md")
    )

    for label in ("工作台", "工作空间", "智能体编排", "AI 线程", "覆盖率分析", "设置"):
        assert label in docs

    for retired in (
        "CodeTalks 前端 | 3000",
        "CodeTalks API | 8000",
        "工具监控",
        "工具页面",
        "导航栏底部",
        "新建分析",
        "Kinetic Shadow",
        "深色主题",
        "设置/状态",
        "DeepWiki",
        "历史任务",
    ):
        assert retired not in docs
