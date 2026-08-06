---
feature_ids: [F014]
topics: [quality-gate, frozen-invocation, skill-runtime, task-9]
doc_kind: quality-gate-report
created: 2026-08-05
---

# F014 Task 9 Frozen Invocation Quality Gate

## Scope

This gate covers the first Task 9 runtime slice: freezing a Skill Version
invocation before execution and proving the common Fake Agent lifecycle
contract. It does not yet claim final OpenCode + DeepSeek V4 Flash acceptance.

## Contract Check

- `skill_run_invocation.py` freezes Skill Version id, Skill id, content digest,
  source ZIP path, IR path, validation report path, inputs, selected deliveries,
  and artifact root into `skill_invocation.json`.
- Invocation freeze rejects Skill content digest drift and missing release
  artifacts.
- Workbench Task run creation writes `task_bundle["skill_invocation"]` and the
  matching artifact file before execution.
- `skill_run_executor.py` defines the provider-independent lifecycle adapter
  boundary and records `agent_run_lifecycle.json`.
- Fake Agent lifecycle covers create, start, event, cancel, timeout,
  session-loss, restart, adapter failure, and normal completion states.

## Fresh Verification

Worktree: `/Volumes/Media/codetalk-skill-first-agent-runtime`

Branch: `codex/skill-first-agent-runtime`

```text
cd backend
/Users/shepard/.local/bin/uv run --with-requirements requirements.txt \
  python -m pytest -q \
  tests/test_skill_run_invocation.py \
  tests/test_skill_run_executor.py \
  tests/test_skill_agent_lifecycle.py
=> 10 passed
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
  tests/test_skill_agent_lifecycle.py
=> 74 passed before Task 10 remediation; then-current Task 7-10 focused gate
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

Independent Task 9 review found that adapter exceptions during `create`,
`start`, or non-terminal `poll` lost already-recorded lifecycle events.
`skill_run_executor.py` now persists `agent_run_lifecycle.json` with a terminal
`failed` event and phase/error metadata before raising `SkillRunExecutorError`;
`test_skill_agent_lifecycle_persists_adapter_failures` covers create, start,
and poll failures.

The same review found the invocation record did not freeze every release
identity claimed by this gate. `skill_run_invocation.py` and
`skill-run-invocation-v1.schema.json` now include `skill_id`, `source_zip`,
`skill_ir`, and `validation_report` references with digests; contract fixtures
and `test_freeze_skill_run_invocation_writes_immutable_execution_record` cover
the expanded frozen record.

## Gate Decision

Task 9 frozen-invocation slice passed independent re-review.

Final reviewer: Hegel (`019fd2cd-c8c9-7502-a57a-374c42bd304b`)

Final verdict: `APPROVE`

The local runtime bridge and frozen invocation evidence may proceed to merge
gate. Formal real-provider evidence remains tracked by the final acceptance
report rather than this backend slice gate.
