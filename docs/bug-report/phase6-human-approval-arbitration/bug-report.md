---
feature_ids:
  - harness-workflow-phase6
topics:
  - human-approval
  - cancellation
  - durability
doc_kind: bug-report
created: 2026-07-28
---

# Phase 6 Human Approval arbitration

| Field | Detail |
|------|--------|
| 1. Symptom | Cancelling a run waiting on a later approval can report that an earlier decision already won, leaving the current wait uncancelled. A forged or corrupted cancellation receipt is accepted without matching its approval identity. Deadline, idempotent resume, and frozen-plan races can also select or schedule the wrong outcome. |
| 2. Evidence | `_claim_waiting_human_approval_cancellation` returns on the first decided approval instead of continuing to the unresolved approval. `load_cancellation_receipt` parses a receipt but does not compare it with the approval record. |
| 3. Root cause | Arbitration is evaluated per compiled-plan node without distinguishing historical terminal approvals from the currently pending approval, and cancellation receipt validation is weaker than expiry receipt validation. |
| 4. Diagnostic strategy | Reproduce both behaviors through the real store and the API cancellation helper, then inspect the same-lock decision/expiry/cancellation call chain. |
| 5. Timeout strategy | If focused store and helper tests do not isolate the behavior within 20 minutes, trace a two-approval V3 attempt through the scheduler and event store. |
| 6. Warning strategy | Any fix that mutates approval records, deletes receipts, or allows more than one durable winner is invalid. |
| 7. User-visible correction | Cancelling a run waiting on a later approval reliably ends the run; corrupt receipt state fails closed instead of being treated as a valid cancellation. |
| 8. Acceptance | New RED tests cover sequential approvals, cancellation receipt identity, trusted receive time, deadline-before-cancellation, atomic first-decision reporting, retry repair, frozen-plan authority, receipt chronology, and monitor shutdown; focused approval/API suites and the full Phase 6 gate must pass. |
