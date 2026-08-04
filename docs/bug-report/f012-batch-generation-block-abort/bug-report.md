---
feature_ids: [F012]
topics: [quality-evaluation, batch-runtime, terminal-block, audit-evidence]
doc_kind: bug-report
created: 2026-08-04
---

# F012 Batch Generation Block Abort

## Reporter And Reproduction

The formal 12-case rapid baseline at CodeTalk revision `1ef2fdd8` exposed the
defect. BMCWeb and FEMU completed, then LMCache ended generation with
`workbench_quality_blocked` after 281.487 seconds. The generator atomically
published `generation_failure.json`, but the corpus CLI raised `RuntimeError`
and did not start the remaining nine cases.

Expected: a terminal block remains local to that case, its immutable evidence
is retained, the batch continues, and the command returns a blocked exit code.
Actual: one valid fallback block aborted the complete baseline and discarded
the opportunity to measure unrelated projects.

## Root Cause

The generator correctly classified and published the terminal failure. The
batch controller had no case-level failure boundary around
`generate_quality_benchmark_artifacts()`, so the exception crossed the corpus
loop. The failure publisher also deleted the Workbench staging tree before
retaining its already-sanitized audit projection, leaving only a 355-byte
summary and hash manifest.

This is an orchestration and evidence-retention defect, not proof that the
LMCache source or model output was unevaluable. The reproduction evidence is
retained under
`/Volumes/Media/codetalk-quality-evidence/f012-baseline-1ef2fdd8-core.run-artifacts/lmcache-local-cpu-put-get-pinned-eviction-recovery-001`.

## Fix

The corpus controller catches only failures that have already published an
immutable `generation_failure.json`, records the case as blocked, continues
the remaining cases, and returns exit code 2 after the loop. Exceptions without
immutable failure evidence still abort.

Before removing Workbench staging, the generator now captures the same
sanitized audit projection used by successful runs and includes it in the
failure artifact hash manifest. No raw credentials, prompt truth, or unverified
candidate response is copied into the failure bundle.

## Verification

Red tests reproduced both defects: the second case was never evaluated after a
first-case generation block, and `workbench_audit.json` was absent. The green
tests prove batch continuation, blocked exit status, immutable failure
retention, sanitized Workbench identity/status, and hash-manifest verification.
The complete F012 quality suite is rerun before the replacement formal baseline
checkpoint is created.

## Replacement Run Result

The replacement rapid run at revision `36a03edc` completed the batch boundary:
10 cases produced immutable evaluations, while Mooncake and SPDK published
terminal `quality_blocked` generator evidence. The command continued through
UCX and returned exit code 2. This confirms the batch fix; it does not convert
the two blocked generators into evaluated samples.
