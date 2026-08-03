---
feature_ids: [F012]
topics: [quality-evaluation, benchmark, accuracy, breadth, depth]
doc_kind: decision
created: 2026-08-03
---

# ADR-025: Independent Three-Axis Quality Evaluation

## Status

Accepted for F012 implementation.

## Context

CodeTalk already validates output structure, source references, and internally declared coverage. Those checks answer whether an output is well formed and whether emitted statements point to source material. They do not independently answer three different questions:

1. Are the statements correct, including important facts the result omitted?
2. Did the analysis cover the independently known behavior surface?
3. Did it close the critical causal chains deeply enough to support tests and conclusions?

Using the generated output to define its own denominator creates a self-scoring loop. Combining different dimensions into one score also permits strong easy dimensions to mask a critical weak dimension. Model agreement has the same limitation: two models can agree and still be wrong or incomplete.

The current failure interaction can also expose a manual retry after a terminal quality block. Making that the ordinary path would impose avoidable user work and discard the opportunity for targeted repair.

## Decision

### Independent truth package

Benchmark cases have evaluator-only gold claims, coverage universes, critical causal chains, and execution oracles. The generating Agent cannot access them. The evaluator loads them only after the run has completed or produced a repair candidate.

### Independent gates

Accuracy, Breadth, and Depth each produce:

- their own numerator and denominator;
- exact misses and critical misses;
- evidence and limitation references;
- an axis status;
- L0/L1/L2/L3 outcomes where applicable.

Final readiness is a conjunction of the axis gates and hard blockers. There is no weighted aggregate quality score.

### Two scopes

Ordinary runs expose an operational audit based on available runtime evidence. Registered benchmark runs add independent benchmark evaluation. The product must not label operational metrics as hidden-truth recall.

### Bounded repair

Repairable quality failures enter a nonterminal automatic-repair loop. The loop preserves accepted artifacts, targets failed obligations, records every attempt, and stops on attempt, elapsed-time, repeated-defect, or no-progress limits. Terminal block is the fallback after those limits or for unrecoverable critical defects.

### Timing semantics

Rapid and deep modes have maximum budgets of 15 and 90 minutes. There is no minimum duration. A result under 5 minutes triggers work-sufficiency diagnostics; it is neither a pass nor a failure by itself.

### Cross-model/version semantics

Running the same pinned case across model or CodeTalk versions is a regression matrix. It can reveal sensitivity, variance, and regressions. It is not ground truth and cannot replace evaluator-only truth.

## Consequences

### Positive

- Accuracy includes omission recall instead of only emitted-claim precision.
- Breadth has an independent denominator.
- Depth measures causal closure instead of document length or source count.
- Critical failures cannot be averaged away.
- Normal repair requires less user intervention.
- Reports remain comparable across model and product versions.

### Costs

- Truth-package authoring and dual review are ongoing work.
- Corpus revisions require deliberate migration and re-baselining.
- L3 execution requires isolated environments and cannot always run.
- The UI and report model must distinguish operational from benchmark quality.

### Failure Modes To Guard

- hidden truth entering prompts, retrieval, bundles, or task artifacts;
- evaluators silently treating missing hardware as a pass;
- generated candidates becoming the only breadth denominator;
- aggregate scores hiding a critical axis;
- automatic repair consuming the entire run without progress;
- model consensus being presented as correctness.

## Alternatives Rejected

### Keep the current `facts` and coverage scores

Rejected because they primarily judge emitted content and internally generated candidates. They cannot measure omitted gold facts or an independently defined behavior surface.

### Use a second model as judge

Rejected as the primary oracle because model agreement is correlated and can reproduce the same omission or misconception. A model judge may assist L2 matching only when constrained by exact evidence and deterministic fixtures.

### Produce one weighted quality score

Rejected because weights permit compensation across non-substitutable dimensions. A critical accuracy error cannot be offset by broad coverage.

### Require a minimum runtime

Rejected because caching, reuse, project size, and machine speed make elapsed time an unreliable quality proxy. Work-sufficiency evidence is more direct.

### Block immediately and ask the user to retry

Rejected as the default interaction because repairable gaps can be targeted automatically while preserving accepted work.

## Validation

ADR compliance is established when F012 acceptance criteria pass, the complete initial corpus has a reproducible baseline, truth-leak tests are green, each axis survives independent adversarial review, and the run cockpit accurately distinguishes repair, limitation, and terminal-block states.
