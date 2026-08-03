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

## Follow-up: Immutable Publication And Freezer Drift

The next formal run reached evaluation but failed while writing the public task projection. The generator had already atomically published its evidence tree as read-only, while the CLI still treated that immutable generator tree as a writable task-run directory.

The same investigation found three related boundary mismatches that fixture-only tests had hidden:

- fresh single-case generation selected `<root>/<case_id>` instead of the documented direct default root;
- generator provenance fields were duplicated into the strict repair summary even though they already exist in `workbench_audit.json`;
- freezer fixtures hashed only candidate directories and used a legacy root field, while production hashes all retained generator files and references `artifact_hash_manifest.json`.

The fix keeps generated evidence immutable, publishes a task-run projection only for an explicitly supplied task-run artifact directory, and restores fresh repair summaries to the strict three-field contract. Current generator evidence hashes every retained file except the exact root control manifest, while the separately published evaluation manifest independently anchors that canonical root. The freezer also recognizes the explicit non-circular legacy first/final-only contract. Recomputed manifests, nested same-name files, wrong anchors, and ambiguous anchor declarations all fail closed. Tests were changed to emulate the production generator shape before implementation; the complete F012 quality suite passed after the corrections (`522 passed`, before the final two anchor-ambiguity cases were added).
