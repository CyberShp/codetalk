---
feature_ids: [F014]
topics: [review-request, frozen-invocation, skill-runtime, task-9]
doc_kind: review-request
created: 2026-08-05
---

# F014 Task 9 Frozen Invocation Review Request

Review-Target-ID: `f014-task9-invocation-slice`

Branch: `codex/skill-first-agent-runtime`

Required reviewer profile: independent reviewer; no self-review.

## Original Requirements

Source: `docs/plans/2026-08-04-f014-skill-first-runtime.md` Task 9.

> Freeze invocation before execution and translate it through the existing
> Harness. The main Agent owns modifications to runner hot files. First make
> every create/start/event/kill/restart/session-loss/cancel/timeout case red
> against the Fake Agent, then implement the smallest common lifecycle contract.

## Handoff

**What:** Review the frozen invocation record and Fake Agent lifecycle contract.

**Why:** Later runner integration must execute the exact approved Skill Version
and frozen inputs, not mutable Task or Draft state.

**Tradeoff:** This slice introduces the stable invocation/executor contract and
Task run freeze point. Full OpenCode + DeepSeek V4 Flash acceptance and deeper
`workbench_workflow_runner.py` adapter integration remain open within Task 9.

**Open Questions:** Look for mutable Skill references, missing digest checks,
missing invocation artifacts, lifecycle states that fail to persist before
raising, and Task run bundles that can diverge from `skill_invocation.json`.

**Next Action:** Return findings first with P0/P1/P2/P3 and exact file/line
references. End with `APPROVE` or `CHANGES_REQUESTED`. Do not edit files or
commit. If the verdict is `CHANGES_REQUESTED`, the main task will reproduce,
fix, rerun evidence, and request re-review until `APPROVE`.

## Review Inputs

- `backend/app/services/skill_run_invocation.py`
- `backend/app/services/skill_run_executor.py`
- `backend/app/api/workbench_v2_tasks.py`
- `backend/tests/test_skill_run_invocation.py`
- `backend/tests/test_skill_run_executor.py`
- `backend/tests/test_skill_agent_lifecycle.py`
- `backend/tests/test_workbench_task_store.py`
- `docs/review-notes/f014-task9-frozen-invocation-quality-gate.md`

## Verification

```bash
cd /Volumes/Media/codetalk-skill-first-agent-runtime/backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_run_invocation.py \
  tests/test_skill_run_executor.py \
  tests/test_skill_agent_lifecycle.py
```

Expected: `7 passed`.

```bash
cd /Volumes/Media/codetalk-skill-first-agent-runtime/backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_store.py \
  tests/test_skill_review.py \
  tests/test_skill_build_pipeline.py \
  tests/test_skills_api.py \
  tests/test_workbench_task_store.py \
  tests/test_skill_run_invocation.py \
  tests/test_skill_run_executor.py \
  tests/test_skill_agent_lifecycle.py
```

Expected: `74 passed` for the Task 9 review-request slice before Task 10
remediation, and `89 passed` for the latest combined backend focused gate after
Task 7-10 remediations.
