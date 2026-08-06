---
feature_ids: [F014]
topics: [review-response, legacy-removal, task-12]
doc_kind: review-response
created: 2026-08-05
---

# F014 Task 12 Legacy Removal Review Response

## Decision

Task 12 received independent `APPROVE` after remediation.

Final reviewer: Kierkegaard (`019fd298-0200-7a12-b55c-e43aba7a3da5`)

Final verdict:

```text
No P0/P1/P2 findings in the requested Task12 scope. The previous stale
frontend/scripts/workbench-run-ui-contract.test.mjs issue is resolved, and I
did not find newly introduced stale Workflow product contract references in
backend/app, frontend/src, or frontend/scripts.

VERDICT: APPROVE
```

## Remediation Summary

| Review round | Verdict | Response |
|---|---|---|
| 1 | `CHANGES_REQUESTED` | Removed legacy frontend client `taskRuns.prepare/run`, old AI task-draft live path, stale Workflow builder scripts, and strengthened the legacy-removal gate. |
| 2 | `CHANGES_REQUESTED` | Removed or rewrote stale frontend contract scripts still importing deleted Workflow builder/AI task-draft paths. |
| 3 | `CHANGES_REQUESTED` | Rewrote `workbench-run-ui-contract.test.mjs` to assert current Skill-first Run Cockpit behavior instead of deleted workbench controller/view files. |
| 4 | `APPROVE` | Reviewer found no remaining P0/P1/P2 in Task12 scope. |

## Final Evidence

```text
frontend scripts/*.test.mjs
=> all 17 script contract files passed

backend tests/test_skill_first_legacy_removal_gate.py
=> 1 passed

stale exposure scan
=> no matches, exit 1

backend route dump
=> NO_BANNED_ROUTES

git diff --check
=> pass
```

## Next Action

Task 12 may proceed to merge gate. Formal intranet acceptance should use the
final acceptance report as its runbook and evidence checklist.
