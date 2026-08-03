---
feature_ids: [F012]
topics: [quality-evaluation, truth-package, semantic-calibration, audit]
doc_kind: review-note
created: 2026-08-03
reviewed: 2026-08-03
verdict: ACCEPT
---

# F012 Truth V6 And Evaluator V4 Audit

## Scope

This note supersedes the truth-v5 calibration checkpoint after retained SPDK
and bmcweb rapid probes exposed evaluator false zeros, false passes, and a
repair re-gate loop. It covers truth package v6 and evaluator v4 only. It does
not approve the P7 numeric baseline, thresholds, timing distribution, or final
Vision gate.

Noether independently reviewed the SPDK findings and semantic material guard.
Heisenberg independently reviewed the bmcweb findings, linked Breadth scenario
semantics, and compound repair re-gating. Both reviews were read-only. The
implementation author did not approve the changes.

## Corrections

- SPDK atomic evidence now binds queue append to `L3173-L3180`; the bounded
  input Depth obligation accepts the independently valid handler EBUSY range.
- BMC gold002 now states only the three-default initialization proven by
  `L3505-L3510`. Recovery node and edge ranges stop at the actual early return.
- Accuracy accepts at most twelve harmless context lines around an atomic
  range and rejects thirteen. Compound detection requires both evidence and
  condition-preserving semantic ownership before repair.
- Correct split successors pass the prepublication re-gate. Retained artifact
  replay identifies only `c-drain-pending` for SPDK and `c-restart-map` plus
  `c-property-absent` for bmcweb.
- Low-effort screening is diagnostic only. It never upgrades a high-effort
  `insufficient` verdict. Missing and failed high-effort adjudication fail
  closed.
- High-effort support passes a deterministic material guard for contradictions,
  conditions, state aliases, values and comparisons, quantifiers, actor roles,
  control return, enumeration completeness, and material ordering.
- Linked Breadth scenarios require both merged-context and scenario-owned
  semantic support. A scenario cannot borrow an unrelated candidate branch.
- The semantic audit v1 keeps the compatibility field
  `candidate_count_by_axis` and adds the honest `judgment_count_by_axis` name.

## Adversarial Closure

The review began with HOLD findings for screening/high disagreement, condition
reversals, shared-range atomic claims, linked scenario evidence, incomplete
truth ranges, and valid paraphrases rejected by deterministic guards. Every
confirmed finding received a failing regression before implementation.

The final regressions include success/failure and comparison reversals,
numeric timeout mismatch, missing EBUSY and authorization conditions,
quantifier and actor omissions, return/order omissions, compound punctuation
bypasses, BMC split-and-re-gate, and valid natural-language forms such as
`at most`, `no more than`, and `nonzero`. Retained BMC text for
`s-property-missing`, `s-unexpected-error`, and `n-recover` is covered as
positive evidence; wrong-state alternatives remain negative.

## Independent Verdicts

- Noether: `PASS`; comparison paraphrases and all previously confirmed SPDK
  and material-guard findings are closed, with no residual P1/P2.
- Heisenberg: `PASS`; the three retained BMC positives support, wrong-state
  negatives fail, and no residual P1/P2 remains.

## Verification

```text
focused runner/semantic/corpus suite: 180 passed
complete quality suite: 617 passed
truth registry: 12 Tier-S cases at truth package v6
evaluator identity: quality-evaluation-v4
semantic judge protocol: quality-semantic-judge-v3
git diff --check: passed
Python compile check: passed
```

The repository-wide backend suite retains the separately owned OpenCode
`--auto` expectation failures documented for the lifecycle iteration. F012 did
not modify those runtime files, and those failures are not relabeled as model
quality failures.

## Verdict

**ACCEPT.** Truth v6 and evaluator v4 are suitable for a clean, immutable P7
calibration checkpoint. Formal 12-case rapid execution, the stratified
rapid/deep comparison, threshold review, baseline freeze, and final P8 audits
remain required.
