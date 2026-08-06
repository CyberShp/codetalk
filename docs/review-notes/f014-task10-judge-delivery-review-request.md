---
feature_ids: [F014]
topics: [review-request, skill-judge, delivery, task-10]
doc_kind: review-request
created: 2026-08-05
---

# F014 Task 10 Judge And Delivery Review Request

Review-Target-ID: `f014-task10-judge-delivery-slice`

Branch: `codex/skill-first-agent-runtime`

Required reviewer profile: independent reviewer; no self-review.

## Original Requirements

Source: `docs/plans/2026-08-04-f014-skill-first-runtime.md` Task 10.

> Prove session isolation, input/artifact scope, `PENDING_VALIDATION -> READY`,
> and full-execution/selective-delivery behavior. A missing optional Judge
> warns; a missing Skill-required Judge prevents READY.

## Handoff

**What:** Review backend Skill Judge status and required-delivery gating.

**Why:** A completed Producer run must not become accepted delivery until a
required Skill Judge validates the frozen invocation/artifact scope.

**Tradeoff:** This slice proves the local Judge/delivery state machine and
delivery blocking. Full real-runtime Judge evidence is still part of final Task
10/12 acceptance.

**Open Questions:** Look for Producer transcript leakage into Judge input,
missing required-Judge blocking, optional Judge incorrectly blocking delivery,
and delivery validation regressions for existing profile outputs.

**Next Action:** Return findings first with P0/P1/P2/P3 and exact file/line
references. End with `APPROVE` or `CHANGES_REQUESTED`. Do not edit files or
commit.

## Review Inputs

- `backend/app/services/skill_judge.py`
- `backend/app/services/workbench_deliverables.py`
- `backend/tests/test_skill_judge.py`
- `backend/tests/test_workbench_deliverables.py`
- `docs/review-notes/f014-task10-judge-delivery-quality-gate.md`

## Verification

```bash
cd /Volumes/Media/codetalk-skill-first-agent-runtime/backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q tests/test_skill_judge.py tests/test_workbench_deliverables.py
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
  tests/test_skill_agent_lifecycle.py \
  tests/test_skill_judge.py \
  tests/test_workbench_deliverables.py
```

Expected: `81 passed` before Task 9 remediation, and `89 passed` for the latest
combined backend focused gate after Task 7-10 remediations.
