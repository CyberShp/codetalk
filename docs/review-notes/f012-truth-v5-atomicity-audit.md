---
feature_ids: [F012]
topics: [quality-evaluation, truth-package, atomicity, calibration, audit]
doc_kind: review-note
created: 2026-08-03
reviewed: 2026-08-03
verdict: ACCEPT
---

# F012 Truth V5 Atomicity And Evaluator Audit

## Scope

This review supersedes the pre-calibration truth counts in the earlier P5
corpus audit. It covers the truth-package and evaluator corrections prompted by
real SPDK and bmcweb rapid probes. The fixed registry contains 12 Tier-S cases,
138 gold claims, and 193 breadth obligations at truth package version 5.

The implementation author did not approve these corrections. Noether reviewed
SPDK, FEMU, nvme-csd, Open-CAS, phosphor-nvme, and phosphor-state-manager.
Heisenberg reviewed bmcweb, LMCache, Mooncake, rdma-core, UCX, and perftest.
Pasteur independently reviewed the evaluator code. All three reviews were
read-only.

## Findings And Disposition

The first truth review found 40 compound gold claims across the 12 projects.
The claims were split into independently judgeable obligations, their source
ranges were narrowed or completed, descriptor hashes were refreshed, and a
mutation gate now rejects every retired compound ID and enforces exact
per-project counts. Follow-up review found six residual compound or incomplete
evidence cases; those were corrected before final acceptance.

The evaluator review found that the first evidence-group implementation could
accept mixed groups and replace them with trusted refs. A second review found
that count equality alone still allowed one wide range to satisfy two required
ranges. The final implementation requires a bijective match between observed
refs and exactly one complete trusted group. The red fixture uses an equal-count
wide-primary plus alternate-group counterexample.

The semantic path now runs low-effort diagnostic screening and high-effort
authoritative adjudication over every judgment. Missing high-effort verdicts
fail closed. The manifest retains a per-judgment trace with axis, screening,
adjudication, and resolved verdicts. Material-clause entailment and evaluator
identity are versioned as semantic judge v3 and evaluator v3.

## Independent Verdicts

- Noether: `ACCEPT`; no remaining P0/P1 across the six assigned projects.
- Heisenberg: `ACCEPT`; no remaining P0/P1 across the six assigned projects.
- Pasteur: `APPROVE`; no remaining P0/P1/P2 in the evaluator correction.

The reviewers independently verified case hashes, source materialization, ID
uniqueness, exact project counts, and the evidence-group counterexamples. No
reviewer modified implementation or truth files.

## Verification

```text
quality suites: 596 passed in 24.96s
corpus and mutation suites: 108 passed in 6.76s
evaluator/corpus integration subset: 307 passed in 17.65s
equal-count mixed-group regression subset: 5 passed
git diff --check: passed
```

The repository-wide backend suite still has inherited failures documented in
`f012-full-backend-parity.md`. The latest fail-fast run stopped at the existing
OpenCode `--auto` argument expectation after 318 passes and 2 skips; no F012
file participates in that failure.

## Verdict

**ACCEPT.** Truth v5 and evaluator v3 are suitable for a clean calibration
checkpoint. This verdict does not approve the P7 numeric baseline itself; fresh
real probes, complete-corpus runs, threshold review, and final Vision review
remain required.
