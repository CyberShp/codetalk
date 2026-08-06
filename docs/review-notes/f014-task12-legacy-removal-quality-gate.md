---
feature_ids: [F014]
topics: [quality-gate, legacy-removal, task-12]
doc_kind: quality-gate-report
created: 2026-08-05
---

# F014 Task 12 Legacy Removal Quality Gate

## Scope

This gate covers the Skill-first product replacement and live Workflow product
surface removal. It removes Workflow authoring routes, Workflow UI routes,
Workflow canvas/contracts, legacy task-draft creation from AI threads, and
legacy frontend run/prepare client methods. It keeps reusable Task, Attempt,
event, checkpoint, artifact, delivery, and Run Cockpit machinery.

## Verification

```text
cd frontend
for f in scripts/*.test.mjs; do node "$f" || exit $?; done
=> all 17 frontend script contract files passed

cd frontend
npm run lint
=> pass

cd frontend
npx tsc --noEmit
=> pass

cd frontend
npm run build
=> pass

cd frontend
CODETALK_BACKEND_PYTHON=/Volumes/Media/codetalk-skill-first-agent-runtime/backend/.venv/bin/python \
CODETALK_REUSE_EXISTING_SERVER=0 \
NEXT_PUBLIC_API_URL=http://localhost:3004 \
npx playwright test e2e/skill-first-task-run.spec.ts --project=chromium
=> 1 passed
```

Backend focused gate:

```text
cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_first_legacy_removal_gate.py \
  tests/test_agent_workbench_api.py::test_task_run_detail_accepts_skill_step_ids_with_dots \
  tests/test_skills_api.py \
  tests/test_workbench_task_store.py \
  tests/test_skill_run_invocation.py \
  tests/test_skill_judge.py \
  tests/test_workbench_deliverables.py \
  tests/test_workbench_artifact_path_authority.py \
  tests/test_skill_store.py \
  tests/test_skill_review.py \
  tests/test_skill_build_pipeline.py \
  tests/test_v3_workflow_runner.py::test_v3_skill_step_requires_frozen_invocation
=> 90 passed
```

Official archive contract/build/review/runtime/Judge gate:

```text
cd backend
CODETALKS_V24_ARCHIVE=/Users/shepard/Downloads/codetalks-fused-v2.4-zh.zip \
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_schemas.py \
  tests/test_skill_source_inventory.py \
  tests/test_skill_package_importer.py \
  tests/test_skill_package_validator.py \
  tests/test_skill_ir_compiler.py \
  tests/test_skill_store.py \
  tests/test_skill_build_pipeline.py \
  tests/test_skill_review.py \
  tests/test_skills_api.py \
  tests/test_skill_run_invocation.py \
  tests/test_skill_run_executor.py \
  tests/test_skill_agent_lifecycle.py \
  tests/test_skill_judge.py \
  tests/test_workbench_deliverables.py \
  tests/test_v3_workflow_runner.py::test_v3_skill_step_requires_frozen_invocation
=> 362 passed
```

Removal/source gates:

```text
rg -n '(createTaskDraft|task-drafts|workflow-builder\.mjs|taskRuns\.prepare\(|taskRuns\.run\(|/api/workbench/(workflows|workflow-presets|workflow-capabilities|workflow-templates|node-registry|core-workflow-readiness|task-runs/prepare|task-runs/run)|workbench_v2_workflows|using-superpowers|Maximum concurrency is three)' \
  backend/app frontend/src frontend/scripts AGENTS.md docs/plans/2026-08-04-f014-skill-first-runtime.md
=> no matches, exit 1

git diff --check
=> pass
```

## Known Full-Suite Residual

A full backend pytest run on this branch reported many stale failures from the
deleted Workflow/Designer/Phase7 product surface plus unrelated legacy failures.
This gate does not restore the deleted Workflow product to satisfy those stale
tests. Remaining full-suite work is a test-suite migration task, not a reason to
reintroduce Workflow product routes.

## Gate Decision

Task 12 passed independent review.

Final reviewer: Kierkegaard (`019fd298-0200-7a12-b55c-e43aba7a3da5`)

Final verdict: `APPROVE`

An additional read-only legacy/source audit by Feynman
(`019fd2ec-9233-76b1-a262-136a213de84a`) also returned `APPROVE` after the final
source gate found no banned Workflow/task-draft/prepare-run references and no
`using-superpowers` references.
