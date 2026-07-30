---
feature_ids:
  - harness-workflow-phase6
topics:
  - human-approval
  - idempotency
doc_kind: bug-report
created: 2026-07-28
---

# Human Approval Decision Idempotency

## Report

- Reporter: Phase 6 main-agent integration review.
- Expected: only an exact retry of the first immutable decision is idempotent.
- Actual: any later request with the same `approve` or `reject` value returned the first record, even when actor, reason, or timestamp differed.

## Diagnosis Capsule

| Field | Evidence |
|---|---|
| Symptom | A second actor's same-direction decision was silently treated as a retry. |
| Evidence | `HumanApprovalStore.decide()` compared only `record.decision.decision`. |
| Root cause | The idempotency key covered the decision enum instead of the complete immutable decision. |
| Diagnostic strategy | Trace request fields through `decide()` and contrast the stored dataclass equality contract. |
| Timeout strategy | Stop after one focused hypothesis test and reassess the record model if it does not pass. |
| Warning strategy | Any need to mutate the first record or accept partial equality invalidates this fix. |
| User-visible correction | Conflicting approval submissions now fail explicitly instead of being silently discarded. |
| Acceptance | Exact replay remains idempotent; same-direction requests with changed actor, reason, or time raise `ApprovalConflict`. |

## Fix And Verification

Construct the complete proposed `ApprovalDecision` before checking for an existing decision and compare the frozen dataclasses for exact equality.

RED:

```bash
PYTHONPATH=backend python3.11 -m pytest backend/tests/test_human_approval_node.py::test_human_approval_waits_records_immutable_decision_and_rebuilds_projection -q
```

The test failed because the conflicting same-direction request did not raise.

GREEN:

```bash
PYTHONPATH=backend python3.11 -m pytest backend/tests/test_human_approval_node.py -q
```

Result: `2 passed`.
