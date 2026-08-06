---
feature_ids: [F014]
topics: [review-request, task-binding, skill-version, task-8]
doc_kind: review-request
created: 2026-08-05
---

# F014 Task 8 Task Binding Review Request

Review-Target-ID: `f014-task8`

Branch: `codex/skill-first-agent-runtime`

Required reviewer profile: independent reviewer; no self-review.

## Original Requirements

Source: `docs/plans/2026-08-04-f014-skill-first-runtime.md` Task 8.

> Replace Workflow binding with Skill Version/digest. Because the product is not
> deployed, use one explicit destructive schema migration with backup and tests;
> do not retain dual Workflow/Skill write paths or a binding table.

## Handoff

**What:** Review Workbench Task storage/API migration from Workflow binding to
immutable Skill Version and content digest.

**Why:** Task 9 runtime invocation must freeze and execute an approved Skill
Version, not a mutable Workflow version or legacy binding row.

**Tradeoff:** The existing run preparer still has legacy parameter names such as
`workflow_id`; Task 8 passes `task.skill_id` through that compatibility surface
until Task 9 replaces runtime invocation. Historical run history remains
read-only.

**Open Questions:** Look for active Task binding authority that still depends on
`workflow_id` or `workflow_version_id`, accidental dual write paths, missing
destructive migration backup, mutable Skill identity updates, run bundles that
fail to freeze Skill version/digest, and API responses that omit Skill Version
authority. Do not report read-only historical run `workflow_id` fields as a
finding unless they can alter new Task binding.

**Next Action:** Return findings first with P0/P1/P2/P3 and exact file/line
references. End with `APPROVE` or `CHANGES_REQUESTED`. Do not edit files or
commit. If the verdict is `CHANGES_REQUESTED`, the main task will reproduce,
fix, rerun evidence, and request re-review until `APPROVE`.

## Review Inputs

- `backend/app/services/workbench_task_store.py`
- `backend/app/api/workbench_v2_tasks.py`
- `backend/tests/test_workbench_task_store.py`
- `backend/app/services/skill_store.py`
- `backend/app/services/skill_build_pipeline.py`
- `backend/app/services/skill_review.py`
- `backend/app/api/skills.py`
- `docs/plans/2026-08-04-f014-skill-first-runtime.md`
- `docs/review-notes/f014-task8-task-binding-quality-gate.md`

## Verification

```bash
cd /Volumes/Media/codetalk-skill-first-agent-runtime/backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q tests/test_workbench_task_store.py
```

Expected: `32 passed`.

```bash
cd /Volumes/Media/codetalk-skill-first-agent-runtime/backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_store.py \
  tests/test_skill_review.py \
  tests/test_skill_build_pipeline.py \
  tests/test_skills_api.py \
  tests/test_workbench_task_store.py
```

Expected: `64 passed`.
