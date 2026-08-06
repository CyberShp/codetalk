---
feature_ids: [F014]
topics: [quality-gate, skill-ui, task-11]
doc_kind: quality-gate-report
created: 2026-08-05
---

# F014 Task 11 Skill UI Slice Quality Gate

## Scope

This gate covers a first Skill-first frontend slice: typed Skill API client,
Skill Version types, a reusable Skill Version summary component, and Task detail
fallbacks that render Skill binding authority. It does not claim final Task
wizard replacement or Playwright Skill-first journey completion.

## Contract Check

- `frontend/src/lib/types/skill.ts` models Skill project/draft/build/review and
  immutable Version payloads.
- `frontend/src/lib/api/skills.ts` wraps `/api/skills` project, draft, import,
  build, review, publish, version, and manifest endpoints.
- `frontend/src/lib/types/task.ts` now includes `skill_id`,
  `skill_version_id`, and `skill_content_digest` while retaining optional legacy
  Workflow fields for transition.
- Task detail renders Skill binding first and falls back to legacy Workflow
  fields only for historical records.
- `frontend/src/features/skills/skill-version-summary.tsx` provides a reusable
  immutable/reviewed Skill Version summary surface.

## Fresh Verification

```text
cd frontend
npm ci
=> installed frontend dependencies; npm audit reports existing dependency
   vulnerabilities.

cd frontend
npm run lint
=> pass

cd frontend
npx tsc --noEmit
=> pass

cd frontend
npm run build
=> pass
```

Backend adjacent gate:

```text
cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_first_legacy_removal_gate.py \
  tests/test_agent_workbench_api.py::test_task_run_detail_accepts_skill_step_ids_with_dots \
  tests/test_skills_api.py \
  tests/test_workbench_task_store.py \
  tests/test_skill_run_invocation.py \
  tests/test_skill_run_executor.py \
  tests/test_skill_agent_lifecycle.py \
  tests/test_skill_judge.py \
  tests/test_workbench_deliverables.py \
  tests/test_workbench_artifact_path_authority.py \
  tests/test_skill_store.py \
  tests/test_skill_review.py \
  tests/test_skill_build_pipeline.py \
  tests/test_v3_workflow_runner.py::test_v3_skill_step_requires_frozen_invocation
=> 90 passed
```

## Gate Decision

Task 11 frontend slice passed independent review.

Final reviewer: Helmholtz (`019fd289-7382-7bb3-9a94-ee8afc1644ed`)

Final verdict: `APPROVE`

The Task wizard and Run Cockpit Skill-first path are covered by the final
frontend static gates and `e2e/skill-first-task-run.spec.ts`, which creates a
Task from a published Skill Version, executes the run, and opens the frozen run
cockpit.
