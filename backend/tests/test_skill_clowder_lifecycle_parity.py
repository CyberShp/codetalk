from __future__ import annotations

from pathlib import Path

import pytest

from app.services.skill_run_executor import (
    CLOWDER_LIFECYCLE_STATUS_MAP,
    SkillRunExecutorError,
    clowder_invocation_status_for_skill_status,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLOWDER_ROOTS = (
    Path("/Volumes/Media/clowder-ai"),
    Path("/Volumes/Media/clowder-ai-runtime"),
    Path("/Volumes/Media/clowder-ai-lifecycle-benchmark"),
)


def test_skill_first_lifecycle_is_compared_against_all_local_clowder_baselines() -> None:
    baselines = []
    for root in _CLOWDER_ROOTS:
        if not root.exists():
            continue
        adr = root / "docs" / "decisions" / "008-conversation-mutability-and-invocation-lifecycle.md"
        runtime = root / "docs" / "features" / "F143-hostable-agent-runtime.md"
        assert adr.exists(), f"{root} is present but ADR-008 is missing"
        assert runtime.exists(), f"{root} is present but F143 is missing"
        baselines.append((root, adr.read_text(encoding="utf-8"), runtime.read_text(encoding="utf-8")))
    if not baselines:
        pytest.skip("local clowder-ai lifecycle baselines are not available")
    assert {root.name for root, _adr, _runtime in baselines} == {
        "clowder-ai",
        "clowder-ai-runtime",
        "clowder-ai-lifecycle-benchmark",
    }

    for root, adr_text, runtime_text in baselines:
        for status in ("queued", "running", "succeeded", "failed", "canceled"):
            assert status in adr_text, f"{root} missing ADR status {status}"
        for requirement in ("events", "cancel", "close", "session", "task"):
            assert requirement in runtime_text, f"{root} missing runtime term {requirement}"

    codetalk_parity = (_REPO_ROOT / "docs" / "features" / "F002-clowder-agent-parity.md").read_text(encoding="utf-8")
    invocation_schema = (
        _REPO_ROOT / "backend" / "app" / "schemas" / "skills" / "skill-run-invocation-v1.schema.json"
    ).read_text(encoding="utf-8")
    task_run_tests = (_REPO_ROOT / "backend" / "tests" / "test_workbench_task_run.py").read_text(encoding="utf-8")
    agent_lifecycle_tests = (
        _REPO_ROOT / "backend" / "tests" / "test_skill_agent_lifecycle.py"
    ).read_text(encoding="utf-8")
    skill_center_e2e = (
        _REPO_ROOT / "frontend" / "e2e" / "skill-center-product-loop-real.spec.ts"
    ).read_text(encoding="utf-8")

    for parity in ("lifecycle stage", "session mode", "artifact-first", "source-first", "E2E coverage"):
        assert parity in codetalk_parity

    assert CLOWDER_LIFECYCLE_STATUS_MAP == {
        "queued": ("created",),
        "running": ("running", "restarted"),
        "succeeded": ("completed",),
        "failed": ("failed", "session_lost", "timed_out"),
        "canceled": ("cancelled",),
    }
    for clowder_status in ("queued", "running", "succeeded", "failed", "canceled"):
        assert clowder_status in CLOWDER_LIFECYCLE_STATUS_MAP
        assert CLOWDER_LIFECYCLE_STATUS_MAP[clowder_status]
        for codetalk_status in CLOWDER_LIFECYCLE_STATUS_MAP[clowder_status]:
            assert clowder_invocation_status_for_skill_status(codetalk_status) == clowder_status
    with pytest.raises(SkillRunExecutorError, match="unknown skill lifecycle status"):
        clowder_invocation_status_for_skill_status("mystery")

    assert '"runtime"' in invocation_schema
    assert '"sessions"' in invocation_schema
    assert "agent_run_lifecycle.json" in task_run_tests
    assert "test_skill_agent_lifecycle_terminal_states" in agent_lifecycle_tests
    assert "requested_provider).toBe(\"opencode\")" in skill_center_e2e
    assert "requested_model).toBe(\"deepseek/deepseek-v4-flash\")" in skill_center_e2e
    assert "declared_context_window_tokens).toBe(200000)" in skill_center_e2e
    assert "requested_max_output_tokens).toBe(4096)" in skill_center_e2e
