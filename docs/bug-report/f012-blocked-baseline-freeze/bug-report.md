---
feature_ids: [F012]
topics: [quality-evaluation, baseline, generation-failure, release-gate]
doc_kind: bug-report
created: 2026-08-04
---

# F012 Blocked Baseline Freeze

## Report

The formal rapid corpus run at revision `36a03edc` observed all 12 registered
cases, but only 10 reached independent evaluation. Mooncake and SPDK stopped at
the generation quality gate and retained immutable failure packages. The
standard freezer correctly rejected this input because numeric threshold
calibration requires 12 evaluable reports.

Discarding the run would lose valid cross-domain evidence. Treating the two
failures as zero-valued axis reports would invent measurements. Retrying until
all generators happened to pass would hide lifecycle reliability.

## Fix

A separate blocked-observation freezer now retains the 10 verified
evaluation/generator pairs and two terminal generator failures. Their case IDs
must be disjoint and together equal the formal 12-case registry. Every failure
is bound to the clean CodeTalk revision, pinned source tree, model identity,
truth-isolation marker, and all-file hash manifest. A `quality_blocked` failure
must also retain its sanitized Workbench audit.

The bundle emits partial per-axis distributions only for the 10 evaluated
cases, marks the release status blocked, and writes
`threshold_freeze_status.json`. It deliberately does not create
`threshold_policy.json`. The ordinary 12/12 freezer and its threshold policy
contract remain unchanged.

## Verification

Tests cover successful 10+2 publication, tampered failure bytes, missing case
coverage, missing Workbench audit, and CLI exit code 2. The complete freezer and
generator test modules pass before formal evidence is frozen.
