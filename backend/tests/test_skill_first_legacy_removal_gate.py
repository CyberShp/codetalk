from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_skill_first_live_surface_has_no_workflow_authoring_routes() -> None:
    removed_paths = [
        REPO_ROOT / "backend/app/api/workbench_v2_workflows.py",
        REPO_ROOT / "backend/app/services/workbench_skills.py",
        REPO_ROOT / "frontend/src/lib/api/workflows.ts",
        REPO_ROOT / "frontend/src/lib/types/workflow.ts",
        REPO_ROOT / "frontend/src/lib/workflow-builder.d.ts",
        REPO_ROOT / "frontend/src/lib/workflow-builder.mjs",
        REPO_ROOT / "frontend/scripts/gate1-workflows-contract.test.mjs",
        REPO_ROOT / "frontend/scripts/phase7-workflow-migration.test.mjs",
        REPO_ROOT / "frontend/scripts/workflow-builder-canvas-contract.test.mjs",
    ]
    assert [path for path in removed_paths if path.exists()] == []

    removed_trees = [
        REPO_ROOT / "frontend/src/app/workflows",
        REPO_ROOT / "frontend/src/features/workflows",
    ]
    leftover_files = [
        path
        for tree in removed_trees
        if tree.exists()
        for path in tree.rglob("*")
        if path.is_file()
    ]
    assert leftover_files == []

    live_sources = [
        REPO_ROOT / "backend/app/main.py",
        REPO_ROOT / "backend/app/api/agent_workbench.py",
        REPO_ROOT / "backend/app/api/ai_conversations.py",
        REPO_ROOT / "frontend/src/lib/api.ts",
        REPO_ROOT / "frontend/src/app/ai/[id]/page.tsx",
        REPO_ROOT / "frontend/scripts/ai-thread-hub-contract.test.mjs",
        REPO_ROOT / "frontend/scripts/app-router-page-contract.test.mjs",
        REPO_ROOT / "frontend/scripts/workbench-v2-release-contract.test.mjs",
        REPO_ROOT / "frontend/scripts/source-driven-mindmap-contract.test.mjs",
    ]
    banned_fragments = [
        "workbench_v2_workflows",
        '"/workflows',
        '"/api/workbench/workflows',
        '"/workflow-presets',
        '"/api/workbench/workflow-presets',
        '"/workflow-capabilities',
        '"/api/workbench/workflow-capabilities',
        '"/workflow-templates',
        '"/api/workbench/workflow-templates',
        '"/node-registry',
        '"/api/workbench/node-registry',
        '"/core-workflow-readiness',
        '"/api/workbench/core-workflow-readiness',
        '"/task-runs/prepare"',
        '"/api/workbench/task-runs/prepare"',
        '"/task-runs/run"',
        '"/api/workbench/task-runs/run"',
        '"/task-drafts"',
        '"/api/ai/conversations/${encodeURIComponent(id)}/task-drafts"',
        "workbench.workflows",
        "workflowCapabilities",
        "coreWorkflowReadiness",
        "createTaskDraft",
    ]
    violations = []
    for source in live_sources:
        text = source.read_text(encoding="utf-8")
        for fragment in banned_fragments:
            if fragment in text:
                violations.append(f"{source.relative_to(REPO_ROOT)} contains {fragment}")
    assert violations == []
