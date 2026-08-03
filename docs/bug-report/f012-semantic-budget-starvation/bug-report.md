---
feature_ids: [F012]
topics: [quality-evaluation, semantic-adjudication, timing, cache]
doc_kind: bug-report
created: 2026-08-04
---

# F012 Semantic Budget Starvation

## Report

- **What:** The first formal rapid BMC case exhausted its 900-second whole-chain
  deadline and published a fail-closed report with all three axes unavailable.
- **Why it mattered:** Generation completed in 258.163 seconds and produced a
  complete artifact tree, but evaluator scheduling prevented an authoritative
  result from being delivered within the rapid contract.
- **Tradeoff:** Semantic adjudication must remain high effort and fail closed;
  reducing judge quality or treating a low-effort screen as authoritative would
  make the timing number look better while weakening Accuracy, Breadth, and
  Depth.
- **Open question at diagnosis:** Whether the delay was inherent model latency or
  avoidable duplicate work.
- **Next action taken:** Trace every semantic invocation, reproduce against the
  frozen generator artifacts, then change scheduling only after the duplicated
  work was measured.

## Root Cause

The evaluator ran a non-authoritative low-effort diagnostic before the
authoritative high-effort adjudication for both the first and final snapshots.
The high-effort idle timeout was only 120 seconds. In the final snapshot, 42 of
46 source/model/mode/content-bound judgments were identical to completed
first-pass judgments, but all were submitted again.

The failure was therefore a deterministic orchestration defect: diagnostic work
consumed critical-path budget, the authoritative timeout was shorter than
observed valid model latency, and unchanged judgments were recomputed. It was
not evidence that CodeTalk failed to generate artifacts.

## Fix

- Only authoritative high-effort adjudication runs on the release-critical
  path; the audit explicitly records that low-effort screening was not run.
- High-effort idle timeout is 300 seconds while the absolute 900/5400-second
  whole-chain deadlines remain authoritative.
- A case-local semantic cache reuses only completed judgments bound to source,
  model, mode, and content. Changed judgments are materialized normally.
- Cache audit records hit/materialized counts and hashes of combined and newly
  materialized results. Missing or failed high-effort judgments still fail
  closed, and deterministic material guards are unchanged.
- Evaluator identity advances from `quality-evaluation-v4` to
  `quality-evaluation-v5`.

## Verification

The frozen BMC generator artifacts were evaluated twice. The uncached v5 probe
completed both high-effort snapshots in 675.800 seconds of evaluation time. The
cache-enabled probe completed the whole chain in 318.855 seconds, including the
original 258.163-second generation phase. Its first snapshot materialized 45
judgments; the final snapshot reused 42 and materialized 4. Both adjudications
completed without semantic limitations.

The final BMC content still failed the three independent axes. This is expected
and important: the change removed duplicate work but did not tune scores or
convert a rejected artifact into a pass.

Regression tests cover high-only authority, missing-high fail closure, cache key
binding, changed-judgment materialization, combined result hashes, generator
cache-state propagation, retained response-byte authority, and coherent hash
declaration forgery. The final complete quality-suite count is recorded in the
P8 gate evidence after implementation and documentation closure.
