---
feature_ids: [F014]
topics: [review-response, frozen-invocation, skill-runtime, task-9]
doc_kind: review-response
created: 2026-08-05
---

# F014 Task 9 Frozen Invocation Review Response

## Decision

Task 9 received independent `APPROVE` after remediation.

Final reviewer: Hegel (`019fd2cd-c8c9-7502-a57a-374c42bd304b`)

Final verdict:

```text
Findings: none for the re-reviewed P1/P2 remediation or direct Task9 regression risk.

P1 is resolved: create/start/poll adapter exceptions now persist
agent_run_lifecycle.json with failed status, phase, error, and error_type before
raising SkillRunExecutorError.

P2 is resolved: skill_invocation.json now freezes skill_id plus release artifact
refs/digests for source ZIP, Skill IR, and validation report, with schema and
fixture coverage updated.

VERDICT: APPROVE
```

## Remediation Summary

| Review finding | Response |
|---|---|
| P1 adapter exceptions during create/start/poll lost lifecycle evidence. | `backend/app/services/skill_run_executor.py` now persists `agent_run_lifecycle.json` with a terminal failed event and phase/error metadata before raising `SkillRunExecutorError`; `backend/tests/test_skill_agent_lifecycle.py` covers create, start, and poll failures. |
| P2 frozen invocation omitted release artifact identities. | `backend/app/services/skill_run_invocation.py`, the schema, fixture, and tests now freeze `skill_id`, source ZIP, Skill IR, and validation report refs plus digests. |

## Final Evidence

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
  tests/test_skill_schemas.py \
  tests/test_skill_run_invocation.py \
  tests/test_skill_run_executor.py \
  tests/test_skill_agent_lifecycle.py \
  tests/test_skill_judge.py \
  tests/test_workbench_deliverables.py
=> 182 passed
```

Additional checks:

- `git diff --check`: pass.

## Next Action

Task 9 may proceed to merge gate. Final acceptance must still include the
Skill-first execute Playwright path and formal real-provider evidence.
