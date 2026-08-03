---
feature_ids: [F012]
topics: [quality-evaluation, timing, calibration, lifecycle-attribution, audit]
doc_kind: review-note
created: 2026-08-04
reviewed: 2026-08-04
verdict: HOLD_PENDING_FORMAL_BASELINE
---

# F012 Evaluator V5 Budget And Calibration Audit

## Scope

This note extends, but does not replace, the accepted truth-v6/evaluator-v4
semantic audit. It covers evaluator-v5 execution scheduling, semantic reuse,
under-five-minute work-sufficiency evidence, and threshold-policy calibration.
It does not approve the P7 numeric baseline or final P8 Vision gate.

## Accuracy, Breadth, And Depth Integrity

The timing correction does not change truth packages, denominators, semantic
prompts, deterministic material guards, axis metrics, critical misses, or the
conjunctive release rule. Cached verdicts are reusable only when source,
provider model, mode, and judgment content are identical. Changed final
judgments are adjudicated at high effort. Therefore the optimization preserves
the prior independent validation semantics for all three axes.

## Time Contract

- Rapid remains bounded by an absolute 900-second whole-chain deadline.
- Deep remains bounded by an absolute 5400-second whole-chain deadline.
- A completed BMC rapid replay measured 318.855 seconds whole-chain.
- Runs below 300 seconds are neither accepted nor rejected by duration alone.
  They require artifact-bound work-sufficiency review.
- Cold and cached under-five-minute runs have separate evidence matrices. A
  cached run must record `status=reused`, `cache_reused=true`, and a cryptographic
  reuse-source identity; a cold run must record `status=sufficient` and no reuse.

## Threshold Calibration

Release thresholds are fixed at `1.0`; observed corpus minima cannot lower them.
For all 12 Accuracy, Breadth, and Depth metrics, calibration constructs a
complete synthetic truth/evidence/candidate package, removes or breaks exactly
one obligation, and invokes the production evaluator. Calibration is accepted
only when the complete candidate scores `1.0`, the mutated metric scores below
`1.0`, and the mutated axis fails. The freezer recomputes this matrix from the
current evaluator and rejects caller-supplied divergence.

Calibration and under-five-minute reviews retain author identities. Reviewer
IDs, roles, and assignments must match the clean, commit-bound
`reviewer_authority.json`; author/reviewer overlap is rejected. Calibration
evidence references must resolve to SHA-256-addressed files copied into the
immutable bundle. Cached work-sufficiency evidence additionally retains the
actual `benchmark_response.json`, whose bytes are rehashed by the freezer and
matched against Workbench, generator, and evaluation declarations. The
threshold policy contract is versioned as `quality-threshold-policy-v3` because
these fields and gates are materially different from v2.

## Lifecycle Benchmark Attribution

The separate CodeTalk-versus-Clowder lifecycle benchmark used pre-final F012
checkpoint `df8c1015`. Its formal SPDK run produced a 125,458-byte rejected
draft and complete structured artifacts, then exposed only a short failure
message after the quality gate scored zero. That observation must not be read as
proof that the model produced no analysis. The benchmark also identified
runtime-owned session persistence and tool-event parsing defects outside F012.

The gate remains strict, but final audit must distinguish generated artifact
quality from whether the product retains and projects rejected artifacts. The
lifecycle report is located at
`/Volumes/Media/codetalk-e2e-artifacts/lifecycle-benchmark/pre-final-f012/REPORT.md`.

## Current Verdict

**HOLD_PENDING_FORMAL_BASELINE.** The BMC replay closes the evaluator timing
regression and confirms that failed content remains failed. The complete 12-case
rapid corpus, stratified rapid/deep matrix, deterministic threshold freeze, and
independent P8 audits are still required before final acceptance.
