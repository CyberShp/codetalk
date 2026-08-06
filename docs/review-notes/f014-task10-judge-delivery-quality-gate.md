---
feature_ids: [F014]
topics: [quality-gate, skill-judge, delivery, task-10]
doc_kind: quality-gate-report
created: 2026-08-05
---

# F014 Task 10 Judge And Delivery Quality Gate

## Scope

This gate covers the backend Judge/delivery slice: Skill Judge status, isolated
Judge input construction, required/optional Judge behavior, and delivery gating.
It does not claim frontend rendering or final real-model Judge acceptance.

## Contract Check

- `skill_judge.py` builds Judge input from frozen `skill_invocation.json`.
- Judge input preserves the frozen artifact root, required artifact ids, and
  Judge artifact ids instead of widening scope to the full task directory.
- Judge input excludes Producer transcript fields.
- Missing required Judge yields `PENDING_VALIDATION` and `ready=false`.
- Missing optional Judge yields `WARNING` without claiming ready.
- `READY` Judge report permits delivery validation to become accepted.
- `workbench_deliverables.py` blocks required Skill delivery acceptance until
  Judge is ready, while preserving existing output/profile validation behavior.

## Fresh Verification

```text
cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_judge.py \
  tests/test_workbench_deliverables.py
=> 7 passed
```

```text
cd backend
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
=> 81 passed before Task 9 remediation; then-current Task 7-10 focused gate
=> 89 passed with Task 7-10 remediations
```

```text
cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_schemas.py \
  tests/test_skill_run_invocation.py \
  tests/test_skill_run_executor.py \
  tests/test_skill_agent_lifecycle.py \
  tests/test_skill_judge.py \
  tests/test_workbench_deliverables.py
=> 182 passed
```

## Review Remediation Notes

Independent Task 10 review found that Judge input widened artifact scope by
using the task directory instead of the frozen invocation artifact scope, and
omitted frozen required/Judge artifact ids. `skill_judge.py` now constructs
`judge_input` from `skill_invocation.json` fields, and
`test_required_skill_judge_moves_from_pending_validation_to_ready` covers
`artifact_root`, `required_artifact_ids`, and `judge.artifact_ids`.

## Gate Decision

Task 10 backend Judge/delivery slice passed independent re-review.

Final reviewer: Aristotle (`019fd2cd-c952-7fa3-a4ad-29a77b8192bf`)

Final verdict: `APPROVE`

The local Judge/delivery authority may proceed to merge gate. Formal
real-provider Judge evidence remains tracked by the final acceptance report
rather than this backend slice gate.
