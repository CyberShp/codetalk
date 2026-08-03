---
feature_ids: [F012]
topics: [quality-evaluation, depth, adversarial-audit, corpus, l3]
doc_kind: review-note
created: 2026-08-03
reviewer_identity: "Codex R3 Depth Auditor"
reviewer_model: "GPT-5 (Codex)"
reviewer_effort: very_high
independence: "Reviewer did not author the Depth evaluator, semantic integration, or corpus truth packages and did not read R1/R2 findings."
reviewed_head: bca88474394cacda5fdc4873f62df8014f7ee3d8
reviewed_worktree: dirty
baseline_status: pending
verdict: ACCEPT
---

# F012 R3 Depth RED-to-GREEN Adversarial Re-review

## Findings

### [P1][resolved confirmed finding] All 12 packages now materialize exact source references

**State: GREEN.** The corpus loader now validates the typed Depth truth, evidence
catalog, execution plan, tier dispositions, case/tier identities, catalog digest, and
the exact set of declared obligations at
`backend/app/services/quality_benchmark_corpus.py:679-779`. When a pinned source is
provided, it materializes every non-L3 range through the production semantic parser at
`backend/app/services/quality_benchmark_corpus.py:781-795`.

The independent probe resolved all 12 repositories at their registry commit and tree,
loaded every case against the real source, and materialized all 328 non-L3 bindings.
Result: `12/12` pinned identities, `12/12` four-file descriptor sets, `12/12` catalog
digests, and `328/328` source/test ranges. This closes the prior bmcweb and
phosphor-state ref-grammar failures and the FEMU and nvme-csd pseudo-path failures.

### [P1][resolved confirmed finding] Every obligation has a semantic oracle, including edge endpoints

**State: GREEN.** `RequiredDepthNode`, `RequiredDepthEdge`, and
`RequiredDisconfirmingCheck` now require a non-empty `statement` at
`backend/app/services/quality_depth_evaluator.py:97-115`. Corpus loading rejects a
missing statement or edge endpoint. The runner's Depth oracle projection appends both
`source_node_id` and `target_node_id` to each edge statement at
`backend/app/services/quality_benchmark_runner.py:1219-1245`.

All 12 `critical_chains.json` files were inspected: all 157 nodes, 145 edges, and 20
checks have statements. An independent projection probe confirmed `145/145` edge
oracles contain the semantic statement and both typed endpoints. One real edge range
per package was then materialized and compared with its statement; no contradiction
was confirmed. A reversed/wrong narrative still fails, and an unavailable semantic
judge returns no support rather than accepting source-range presence alone.

### [P1][resolved confirmed finding] Direct evaluator requires the complete evidence set

**State: GREEN.** Static obligation closure now requires submitted refs to be unique
and exactly equal to the trusted catalog set at
`backend/app/services/quality_depth_evaluator.py:1435-1460`. Per-chain L3 applies the
same exact-set condition at
`backend/app/services/quality_depth_evaluator.py:1552-1573`. Runner alignment first
requires all bound ranges and then emits the evaluator-owned binding refs at
`backend/app/services/quality_benchmark_runner.py:1582-1645`.

Independent non-destructive mutations added a second trusted range while candidates
submitted only the first. Both the static node and Tier E L3 candidate failed closed:

```text
STATIC_SUBSET=FAIL_CLOSED
L3_SUBSET=FAIL_CLOSED
```

The same probe supplied one complete deep chain and one shallow chain. The average was
higher than the minimum, but the weakest-chain gate still failed, so the complete flow
could not mask the shallow flow. A single public observation also closes an obligation
only when it has exactly one supported truth match at
`backend/app/services/quality_benchmark_runner.py:1591-1611`.

### [P1][resolved confirmed finding] Runner owns E/H execution and L3 outcomes

**State: GREEN.** Execution truth now has a typed plan whose E/H policy must be either
allowlisted with an oracle or unavailable with an explicit limitation; Tier S must be
disabled without limitations at
`backend/app/services/quality_depth_evaluator.py:405-488`. Command argv is evaluator
owned, absolute, digest-bound, and restricted to one fixture placeholder at lines
491-508.

The benchmark runner reads the hidden plan and executes it before reading/aligning the
candidate at `backend/app/services/quality_benchmark_runner.py:259-299` and
`407-476`. It passes only evaluator-produced L3 evidence into the aligned candidate at
lines 378-404 and 1648-1650. The executor requires exact plan/catalog refs, an
evaluator allowlist entry, command and fixture hashes, an OS sandbox with network
disabled, a shared deadline, an immutable audit, and the expected result hash at
`backend/app/services/quality_depth_evaluator.py:687-924`.

An independent temporary Tier E case was run through `evaluate_artifact_snapshot`, not
through the executor alone. The runner executed an allowlisted command in the active
macOS sandbox, recorded the expected result hash, injected evaluator L3 `pass`, and
discarded a generator-authored forged L3 pass. Separate S/E/H probes produced:

```text
RUNNER_E=PASS_EVALUATOR_ONLY
S=NOT_APPLICABLE
E_UNAVAILABLE=NOT_RUN
H_UNAVAILABLE=NOT_RUN
```

Unallowlisted commands did not execute. Network access was denied by the OS sandbox,
and a deadline-killed oracle left no running process. Result mismatch or command
failure is L3 `fail`; missing sandbox, source, allowlist, fixture, environment, or
deadline is explicit `not_run`. Neither an E/H limitation nor hardware absence can
become a full pass.

### [P1][pending evidence][baseline only] Frozen baseline is not yet available

**State: PENDING, not a code/corpus rejection.** No frozen complete-corpus output,
per-project/per-domain distribution, or calibrated release threshold was reviewed.
This remains an AC9 and final F012 release dependency. It does not replace or weaken
the code/corpus verdict in this note.

### [P2][confirmed coverage limitation] Real cases are Tier S and single-chain

All 12 current cases contain one critical chain and use Tier S. The weakest-chain
anti-masking gate and evaluator-owned E/H runner therefore have strong synthetic and
runner-integration evidence but no real-corpus E/H execution sample or real multi-chain
holdout. This is disclosed as coverage debt, not as an E/H or hardware pass claim.

## Verdict

**ACCEPT for the F012 Depth code and corpus scope.** The four original P1 findings have
independent RED-to-GREEN evidence. AC4 Depth semantics and the Depth portions of AC7
and AC10 are approved at the reviewed dirty snapshot. Baseline calibration remains
explicitly pending for AC9, so this note does not approve final F012 release or numeric
thresholds.

This review did not read R1/R2 findings and edited only this note. Because the reviewed
implementation and corpus are uncommitted, the eventual frozen baseline must record a
clean implementation SHA and rerun the descriptor/digest and complete quality gates.

## Corpus Evidence

| Project | Nodes/edges/checks | Exact non-L3 refs | Result |
|---|---:|---:|---|
| bmcweb | 13/12/2 | 27 | GREEN |
| FEMU | 13/12/1 | 26 | GREEN |
| LMCache | 14/13/2 | 29 | GREEN |
| Mooncake | 13/12/2 | 33 | GREEN |
| nvme-csd | 13/12/1 | 26 | GREEN |
| Open-CAS | 13/12/1 | 26 | GREEN |
| perftest | 13/12/2 | 27 | GREEN |
| phosphor-nvme | 13/12/2 | 27 | GREEN |
| phosphor-state-manager | 13/12/2 | 27 | GREEN |
| rdma-core | 13/12/2 | 27 | GREEN |
| SPDK | 13/12/1 | 26 | GREEN |
| UCX | 13/12/2 | 27 | GREEN |

The source samples included one edge from every package and covered route or request
entry, bounds and preconditions, ownership inputs, and error transitions. The full
loader probe, rather than only those manually inspected samples, materialized every
catalog range.

## Commands And Evidence

All commands ran from `/Volumes/Media/codetalk-quality-eval-baseline` with bytecode and
pytest cache writes disabled.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend backend/.venv/bin/pytest -q \
  -p no:cacheprovider \
  backend/tests/test_quality_depth_evaluator.py \
  backend/tests/test_quality_benchmark_runner.py \
  backend/tests/test_quality_benchmark_corpus.py \
  backend/tests/test_quality_corpus_mutations.py \
  backend/tests/test_quality_benchmark_semantic_judge.py
# 212 passed in 7.25s

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend backend/.venv/bin/pytest -q \
  -p no:cacheprovider backend/tests/test_quality*.py
# 500 passed in 23.92s
```

The author's retained evidence stated 207 focused and 499 full quality passes. Current
collection is 212 focused and 500 full; the independent current-tree runs therefore
supersede, rather than contradict, those earlier counts.

The explicit adversarial selection covered broken causal/oracle edges, missing
error/cleanup/recovery, weakest-chain masking, static and L3 proper subsets,
unallowlisted execution, sandbox network denial, shared deadline/process cleanup,
wrong narrative, endpoint-bearing edge oracles, and unavailable judge:

```text
11 passed in 0.85s
```

Read-only corpus and structure probes produced:

```text
cases=12 refs=328 descriptors=12/12 digests=12/12 pinned=12/12
statements=322 nodes=157 edges=145 checks=20 endpoint_oracles=145
STATIC_SUBSET=FAIL_CLOSED L3_SUBSET=FAIL_CLOSED WEAKEST_CHAIN=FAIL_CLOSED
RUNNER_E=PASS_EVALUATOR_ONLY S=NOT_APPLICABLE
E_UNAVAILABLE=NOT_RUN H_UNAVAILABLE=NOT_RUN
```

`git diff --check` passed before the note update. No baseline freeze was run; baseline
output is intentionally recorded as pending and was not used to determine this
code/corpus verdict.
