---
feature_ids:
  - F012
topics:
  - quality-evaluation
  - benchmark-contract
doc_kind: bug-report
created: 2026-08-03
---

# F012 Repair Summary Contract Drift

## Report

- **Reporter:** Codex, during the first formal 12-case benchmark run.
- **Reproduction:** Generate a benchmark case through the real Workbench path, then pass its `repair_summary.json` to the evaluator CLI. Generation succeeds; evaluation raises a Pydantic `extra_forbidden` error for `first_provenance` and `final_provenance`.
- **Expected:** Generator audit metadata remains available while the evaluator receives the strict three-field `RepairSummary` contract.
- **Actual:** The CLI forwarded the complete generator metadata object into the strict evaluator model.

## Root Cause

The generator correctly persisted provenance beside repair timing and terminal state, but the CLI treated that broader file as if it were already the evaluator-owned `RepairSummary`. Unit tests constructed only the three evaluator fields, so they did not exercise this component boundary.

## Fix

The CLI now explicitly projects `attempt_count`, `elapsed_seconds`, and `terminal_block_reason`, validates that projection with `RepairSummary`, and passes only the validated result to evaluation. Unknown generator metadata is retained in generator evidence; missing or invalid evaluator fields still fail closed. The strict evaluator contract remains unchanged.

## Verification

- A regression test includes both provenance fields in the real CLI input and asserts that only the validated evaluator fields cross the boundary.
- The test failed before the fix and passed after it.
- Runner and generator suites passed together (`75 passed`).

