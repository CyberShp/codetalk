---
feature_ids: [F014]
topics: [quality-gate, task-binding, skill-version, task-8]
doc_kind: quality-gate-report
created: 2026-08-05
---

# F014 Task 8 Task Binding Quality Gate

## Scope

This gate covers Task 8 only: Workbench Task binding now points to immutable
Skill Version identity and content digest. It does not implement frozen runtime
invocation, Judge validation, Skill-first frontend UI, or legacy route deletion.

## Contract Check

- `WorkbenchTask` authority fields are `skill_id`, `skill_version_id`, and
  `skill_content_digest`.
- New `workbench_tasks` schema version is `3`; legacy Workflow-bound schemas
  trigger an explicit destructive rebuild after backup.
- New Task API payloads require `skill_version_id` and reject `workflow_id` /
  `workflow_version_id` as forbidden fields.
- Task create resolves the Skill Version through `SkillStore`, stores
  `version.skill_id` and `version.content_digest`, and strips workspace inputs
  from persisted user values.
- Task detail and compile return Skill Version, Skill IR, Skill plan, and
  selected deliveries.
- Task update and clone preserve immutable Skill identity and digest.
- Run creation freezes `skill_version_id`, `skill_content_digest`, compiled
  Skill plan, and effective Skill compatibility definition into the run bundle.
- Historical run listing remains read-only and may expose legacy `workflow_id`
  fields; it is not Task binding authority.

## Red-Green Evidence

Initial Task 8 full file run was red after the first migration pass:

```text
cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q tests/test_workbench_task_store.py
=> 27 passed, 5 failed
```

The five failures were legacy Workflow product assertions that still expected
Task creation through `workflow_id` / `workflow_version_id`. They were migrated
to Skill-first behavior and legacy-payload rejection. During the migration, a
new red failure exposed that Skill IR workspace inputs use `input_id`; run
preparation still indexed workspace inputs by `definition["id"]`. The API now
uses `_input_definition_id` consistently. Another red failure exposed that run
bundles were not receiving frozen Skill metadata because the assignment block
was indented after an exception raise; it is now stored on every prepared run.

## Fresh Verification

Worktree: `/Volumes/Media/codetalk-skill-first-agent-runtime`

Branch: `codex/skill-first-agent-runtime`

```text
cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q tests/test_workbench_task_store.py
=> 32 passed
```

```text
cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_store.py \
  tests/test_skill_review.py \
  tests/test_skill_build_pipeline.py \
  tests/test_skills_api.py \
  tests/test_workbench_task_store.py
=> 64 passed
```

## Sub-Agent Evidence

- `Ampere` performed read-only Task 8 workflow-authority scanning and found no
  confirmed active Task binding path using Workflow identity. It flagged the
  stale unreachable workflow test bodies, which were removed.
- Main integrator owned shared hot paths:
  `backend/app/services/workbench_task_store.py`,
  `backend/app/api/workbench_v2_tasks.py`, and
  `backend/tests/test_workbench_task_store.py`.

## Gate Decision

Task 8 author/integrator self-check is ready for independent review. This is
not approval and does not authorize Phase C completion by itself.
