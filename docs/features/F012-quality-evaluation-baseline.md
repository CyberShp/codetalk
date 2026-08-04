---
feature_ids: [F012]
topics: [quality-evaluation, benchmark, accuracy, breadth, depth, automatic-repair]
doc_kind: feature
created: 2026-08-03
---

# F012: Independent Quality Evaluation Baseline

## Status

- **State**: blocked (P7 formal baseline: 10/12 evaluable)
- **Priority**: P0
- **Owner**: Codex
- **Related**: F002, F008, F009, F011
- **Decision**: [ADR-025](../decisions/adr-025-independent-three-axis-quality-evaluation.md)
- **Execution plan**: [F012 implementation plan](../plans/2026-08-03-f012-quality-evaluation-baseline.md)

## Why

CodeTalk currently audits whether generated artifacts are structurally valid and whether emitted claims have source references. That is useful, but it cannot independently prove that the analysis is correct, broad enough, or deep enough. In particular, an internally generated candidate list cannot be its own coverage denominator, and evidence attached to emitted claims cannot reveal important omitted facts.

The product must also avoid making a terminal block and a manual retry the normal recovery path. As the user stated:

> 有效阻断只能是兜底策略，如果每次分析都阻断，需要用户点击重试，用户体验极差。

The requested operating limits are:

- rapid analysis: no more than 15 minutes;
- deep analysis: no more than 90 minutes;
- no minimum duration;
- a completion under 5 minutes is an anomaly signal to inspect, not an automatic failure;
- automatic, bounded repair is the normal response to repairable quality gaps;
- terminal blocking is reserved for exhausted repair or unrecoverable critical defects.

## Goal

Build an independent, reproducible quality evaluation system that validates every benchmarked CodeTalk result on three non-substitutable axes:

1. **Accuracy**: factual claims are supported, applicable gold facts are not omitted, and critical conclusions are not contradicted.
2. **Breadth**: independently defined entries, flows, branches, states, resources, boundaries, failures, recovery paths, concurrency behavior, and protocol obligations are covered or explicitly disposed.
3. **Depth**: critical causal chains close from external trigger through source behavior, state/resource effects, failure propagation, cleanup/recovery, and an observable or executable oracle.

The same system must guide bounded automatic repair during ordinary runs and provide an independent benchmark mode whose truth package is never exposed to the generating Agent.

## Product Contract

### Two quality scopes

- **Operational quality** runs on every CodeTalk task. It audits schema, provenance, internally declared coverage, work sufficiency, and repair state. It must not present benchmark recall as if a hidden truth package were available.
- **Independent benchmark quality** runs only for registered benchmark cases. It evaluates the completed output against a hidden truth package after generation and records reproducible Accuracy/Breadth/Depth results.

The UI and report schema must identify the scope explicitly. Operational audit results cannot be labeled as independent benchmark scores.

### Three independent axes

Each axis owns its denominator, numerator, critical misses, evidence references, and gate status. The delivery gate is a conjunction of the three axis gates and hard blockers. There is no weighted aggregate score that can hide a weak axis.

### Repair before block

Repairable gaps transition the run into a nonterminal `quality_repairing` state. The repair loop preserves accepted work, targets failed obligations, and is bounded by attempt and time budgets. A user-facing retry is only offered after bounded repair is exhausted or the failure is unrecoverable.

### Timing

- Rapid mode has a 15-minute wall-clock budget.
- Deep mode has a 90-minute wall-clock budget.
- There is no minimum duration.
- Under 5 minutes triggers a work-sufficiency check using evidence reads, traversed calls/branches, claims, critical coverage, cache/reuse state, and unresolved obligations.
- If the work-sufficiency check fails while budget remains, CodeTalk continues or repairs automatically; elapsed time alone never proves quality.

## Benchmark Corpus

The tracked corpus registry must include pinned revisions and cases across all requested domains:

| Domain | Initial projects |
|---|---|
| Storage controller/card and emulation | SPDK, FEMU, nvme_csd |
| Host NVMe/cache stack | Open-CAS |
| BMC and storage management | phosphor-nvme, phosphor-state-manager, bmcweb |
| KV Cache | LMCache, Mooncake |
| RDMA | rdma-core, UCX, perftest |
| RoCE | UCX and perftest cases with RoCE-specific configuration and semantics |

Repository aliases such as `main`, `master`, or release tags are not valid benchmark revisions. Every case pins an immutable commit and records origin URL, content hash, license metadata, execution tier, and truth-package version.

Execution tiers:

- **S: static deterministic**: source and artifact evaluation without executing untrusted project code;
- **E: software executable**: allowlisted builds, emulators, unit tests, or protocol tools in an isolated environment;
- **H: hardware backed**: real devices or network fabric, reported separately and never inferred from S/E results.

Unavailable hardware yields `L3_NOT_RUN` or a limited result. It cannot be reported as a full pass.

## Truth Package

Each benchmark case contains four independently authored components:

1. `gold_claims.json`: applicable facts, criticality, exact evidence, and allowed semantic variants.
2. `coverage_universe.json`: an external coverage denominator organized by domain dimension.
3. `critical_chains.json`: ordered causal nodes/edges and mandatory closure points.
4. `execution_oracles.json`: allowlisted commands, fixtures, expected observations, and execution tier.

Truth packages are loaded only by the evaluator after task completion. They must not be copied into task input, task bundles, prompts, retrieval indexes, or generation artifacts.

## Acceptance Criteria

### AC1: Evaluation contract

- [x] A versioned strict schema represents benchmark identity, source revision, truth-package version, run identity, scope, axis results, first-pass results, final-after-repair results, repair summary, and limitations.
- [x] Invalid or incomplete reports fail closed with exact validation errors.
- [x] Reports contain no aggregate score that combines Accuracy, Breadth, and Depth.
- [x] Operational and independent benchmark scopes are impossible to confuse in schema or UI.

### AC2: Accuracy

- [x] Claim precision is measured against all factual claims emitted by the result.
- [x] Gold recall is measured against all applicable independent gold claims.
- [x] Critical contradictions and unsupported critical claims are hard failures.
- [x] L0 schema, L1 exact provenance, L2 semantic entailment, and L3 executable-oracle outcomes are recorded separately.
- [x] Omitted gold claims reduce recall even when every emitted claim has a citation.

### AC3: Breadth

- [x] The denominator comes from the independent coverage universe, not only generated candidates.
- [x] Results include discovery recall, critical coverage, scenario realization, and disposition completeness.
- [x] Dimensions include entry, flow, branch, state, resource, boundary, concurrency, error/recovery, and protocol/history/mutation obligations where applicable.
- [x] An uncovered critical item cannot be hidden by high coverage in easier dimensions.

### AC4: Depth

- [x] Critical chains are evaluated from trigger and preconditions through entry/call chain, state/resource mutation, downstream effect, failure propagation, recovery/cleanup, and observable/executable oracle.
- [x] Results include minimum critical-chain closure, average closure, state closure, resource-lifecycle closure, error/recovery closure, disconfirming checks, and L3 status.
- [x] One deeply analyzed flow cannot compensate for a shallow or missing critical flow.

### AC5: Automatic repair and blocking

- [x] Repairable failures enter bounded automatic repair without requiring a user click.
- [x] Accepted artifacts are preserved and only failed obligations are targeted.
- [x] Every repair attempt records cause, input obligations, changed artifacts, elapsed time, and outcome.
- [x] Infinite loops are prevented by attempt, elapsed-time, repeated-defect, and no-progress guards.
- [x] Terminal block occurs only after repair exhaustion or an unrecoverable critical failure.

### AC6: Time and work sufficiency

- [x] Rapid runs stop, degrade, or explicitly limit scope by 15 minutes.
- [x] Deep runs stop, degrade, or explicitly limit scope by 90 minutes.
- [x] No minimum duration is enforced.
- [x] Under-5-minute completion invokes work-sufficiency diagnostics and can auto-continue while budget remains.
- [x] Reused/cached work is distinguishable from a genuinely cold run.

### AC7: Corpus

- [x] The registry includes every project and domain listed in the Benchmark Corpus section.
- [x] Every registered project has a pinned commit and immutable manifest metadata.
- [x] Every domain has at least one critical static case; E/H cases declare environment requirements and limitations.
- [x] Corpus source trees and generated outputs are not committed to the product repository.
- [x] Registry integrity, truth leakage, and stale/missing revision checks are automated.

### AC8: Product integration

- [x] Existing run cockpit shows the three axes, quality scope, automatic-repair progress, and terminal limitations without adding a separate dashboard.
- [x] Axis details disclose exact failed claims, missed universe items, and open chain nodes progressively.
- [x] The normal path does not display a manual retry button while automatic repair remains possible.
- [x] Desktop screenshots pass at 1440x900 and 1280x800; mobile passes at 390x844 with no overlap or truncated controls.

### AC9: Baseline and release gate

- [x] A repeatable command runs one case, one domain, or the complete corpus and writes immutable machine-readable plus human-readable reports.
- [ ] Baseline calibration records per-project and per-domain distributions before numeric release thresholds are frozen.
- [x] Release policy gates every axis independently and identifies critical failures separately.
- [ ] Cross-model/version execution is used as regression sampling, not as ground truth or a substitute for hidden-truth evaluation.

### AC10: Independent audit

- [x] The author of an evaluator does not approve that evaluator.
- [x] Accuracy, Breadth, and Depth receive independent adversarial fixture review.
- [x] A Vision Guardian verifies the product contract, especially repair-before-block, no aggregate masking, truth isolation, all-domain coverage, and timing semantics.
- [ ] Quality-gate, request-review, receive-review, and merge-gate evidence is retained.

## Non-Goals

- Building a new quality dashboard or corpus-management UI.
- Treating model agreement, output length, elapsed time, or citation count as ground truth.
- Executing arbitrary commands from external repositories.
- Claiming hardware behavior from static or emulated evidence.
- Freezing universal numeric thresholds before the corpus baseline exists.
- Vendoring third-party source repositories into CodeTalk.

## Risks And Controls

| Risk | Control |
|---|---|
| Gold leakage into generation | Separate paths and processes; post-run loading; automated bundle/prompt/retrieval scans |
| Self-scoring | Independent denominators and evaluator-only truth package |
| Benchmark overfitting | Holdout cases, truth-package versioning, domain-balanced reporting |
| Corpus drift | Immutable commits, origin/hash verification, explicit update review |
| Aggregate masking | Conjunctive gates and per-axis critical failures; no aggregate score |
| Repair loop churn | Attempt/time/no-progress/repeated-defect guards |
| Hardware unavailable | Explicit tier and `L3_NOT_RUN`; no full-pass claim |
| Unsafe third-party execution | Allowlist, isolation, resource caps, no network by default, no raw block devices |
| Manual truth cost | Start with critical cases, require dual review, expand by measured domain gaps |

## Key Decisions

1. Accuracy, Breadth, and Depth are independent gates with independent denominators.
2. Hidden truth is evaluator-only and loaded after generation.
3. Automatic repair is the normal path; terminal block is a last resort.
4. Time is a budget and anomaly signal, not a quality score or minimum-work proxy.
5. Cross-model/version comparison detects regressions but does not establish truth.
6. Benchmark reports are immutable artifacts; no new quality dashboard or database is required.
7. Exact thresholds are calibrated from the first complete baseline and then versioned.

## Formal Baseline Evidence

The formal evidence is frozen at
`/Volumes/Media/codetalk-quality-evidence/f012-baseline-blocked-c193eb2c`.
`baseline_manifest.json` has SHA-256
`0e1c49ac9631cfc1530afd81244a0263807445ac2c6950eb0200af24d1daea2d`
and binds 330 read-only artifacts. The complete independent audit is retained
in [the final blocked-baseline review](../review-notes/f012-final-blocked-baseline-audit.md).

| Gate | Formal result |
|---|---|
| Corpus attempt coverage | 12/12 |
| Evaluable coverage | 10/12 |
| Generation failures | Mooncake and SPDK, both `quality_blocked` |
| Core rapid p100 | `462.808707s`, pass against 15 minutes |
| Four-domain paired rapid p100 | `397.898292s`, pass |
| Four-domain paired deep p100 | `872.87439s`, pass against 90 minutes |
| Under-five work sufficiency | pass after independent review of BMCWeb and NVMe-CSD |
| Threshold policy | not frozen |
| Release | blocked |

The 10 evaluated reports all have `not_ready` delivery and fail Accuracy,
Breadth, and Depth. Their critical-failure counts are 95, 138, and 246
respectively. These are measurements of the retained generated reports, not a
claim that the model produced no output. The two generation failures are not
assigned invented zero scores.

The release reasons are independently derived as
`generation_failures_present`, `thresholds_not_frozen`, and
`repair_attempt_audit_unavailable`. The last reason is specific to the legacy
`36a03edc` failure packages; current code retains canonical repair traces for
future blocked runs.

## Open Questions

- Exact per-axis numeric thresholds remain blocked until all 12 corpus cases produce evaluable baseline distributions.
- Tier H scheduling and lab ownership remain environment-dependent; Tier S/E completion is not blocked by hardware availability.
- Alternative-model and accepted historical-baseline regression samples remain unavailable for AC9.

## Requirements Checklist

- [x] User value and failure mode are explicit.
- [x] Operational and benchmark journeys are separated.
- [x] Scope, non-goals, timing, and repair behavior are explicit.
- [x] Data ownership and truth isolation are explicit.
- [x] Error, limitation, and hardware-unavailable states are explicit.
- [x] Security controls for third-party execution are explicit.
- [x] Test, audit, and no-self-review requirements are explicit.
- [x] UI integration follows progressive disclosure and avoids a new dashboard.
- [ ] Numeric release thresholds are calibrated and frozen after the first baseline.

## Convergence Check

- **New architecture decision**: yes, recorded in ADR-025.
- **Reusable incident lesson**: no confirmed incident is being generalized; the prior `facts` score is treated as a known measurement limitation and covered by this feature contract.
- **New operating rule**: no repository-wide governance change is needed; F012 owns the quality-evaluation contract.
- **Ready for implementation**: yes, through the linked Goal-mode execution plan.
