---
feature_ids: [F012]
topics: [quality-evaluation, breadth, independent-audit, corpus, semantic-entailment]
doc_kind: review-note
created: 2026-08-03
updated: 2026-08-03
review_round: R2-bare-evaluator-proper-subset-retest
reviewer_identity: R2 Breadth Auditor
reviewer_model: Codex GPT-5
reviewer_effort: xhigh
independence: did_not_author_breadth_semantic_integration_or_corpus
worktree: /Volumes/Media/codetalk-quality-eval-baseline
git_head: bca88474394cacda5fdc4873f62df8014f7ee3d8
allowed_write_paths: [docs/review-notes/f012-breadth-audit.md]
forbidden_review_inputs:
  - docs/review-notes/f012-contract-accuracy-audit.md
  - docs/review-notes/f012-depth-audit.md
baseline_distribution_verification: pending_non_blocking
verdict: ACCEPT
---

# F012 R2 Breadth Final Re-review

## Findings

No open Breadth implementation or corpus findings remain at this working-tree snapshot.

The previous P1, "the bare evaluator closes a three-range critical obligation from any non-empty proper subset," is `ruled out at revised snapshot`. The evaluator now verifies a potential match only when every evidence ref declared by the truth item is present in the scenario evidence ([quality_breadth_evaluator.py:208](../../backend/app/services/quality_breadth_evaluator.py)). The one-observation/one-obligation cardinality guard remains immediately after that complete-evidence check ([quality_breadth_evaluator.py:213](../../backend/app/services/quality_breadth_evaluator.py)).

## Independent Red-Green Reproduction

The committed regression has six parameters covering every non-empty proper subset of a three-range obligation ([test_quality_breadth_evaluator.py:481](../../backend/tests/test_quality_breadth_evaluator.py)). The test keeps all other universe obligations realized, so each failure is attributable only to incomplete protocol evidence.

I independently repeated the mutation against the real perftest universe:

| Protocol evidence supplied | Whole axis | Protocol realization | Critical miss |
|---|---|---|---|
| `L618-L640` only | `FAIL` | 0/1 | protocol item present |
| `L652-L700` only | `FAIL` | 0/1 | protocol item present |
| `L915-L928` only | `FAIL` | 0/1 | protocol item present |
| `L618-L640` + `L652-L700` | `FAIL` | 0/1 | protocol item present |
| `L618-L640` + `L915-L928` | `FAIL` | 0/1 | protocol item present |
| `L652-L700` + `L915-L928` | `FAIL` | 0/1 | protocol item present |
| all three ranges | `PASS` | 1/1 | none |

This closes the bare-evaluator trust-boundary gap: no runner alignment or semantic adapter was used in the independent mutation.

## Real Perftest Corpus

The real Tier-S case `perftest-roce-gid-selection-001` parses as a `QualityBenchmarkCase`, and its Breadth universe evaluates directly through `evaluate_breadth_details`.

The repaired critical protocol narrative remains fully source-entailing:

- [perftest_communication.c:618](/Volumes/Media/codetalk-quality-corpus/sources/perftest/src/perftest_communication.c:618) defines RoCE v1 score 1 and RoCE v2 score 2.
- [perftest_communication.c:652](/Volumes/Media/codetalk-quality-corpus/sources/perftest/src/perftest_communication.c:652) applies that version rate to automatic GID scoring and rejects invalid entries.
- [perftest_communication.c:915](/Volumes/Media/codetalk-quality-corpus/sources/perftest/src/perftest_communication.c:915) separates direct user-selected lookup from automatic best-index selection and checks selection/query failures.

The all-three positive result proves that tightening the bare evaluator did not make the valid real obligation uncloseable.

## Integrity Checks

### Single owner

Each exact evidence identity still has one owner only:

| Evidence ref | Owner |
|---|---|
| `source://src/perftest_communication.c#L618-L640` | `perftest-gid:protocol-roce-gid-selection` |
| `source://src/perftest_communication.c#L652-L700` | `perftest-gid:protocol-roce-gid-selection` |
| `source://src/perftest_communication.c#L915-L928` | `perftest-gid:protocol-roce-gid-selection` |

The committed all-universe single-owner mutation also passes.

### Descriptor hash

The descriptor at [case.json:9](../../benchmarks/quality/projects/perftest/perftest-roce-gid-selection-001/case.json) and the independently computed bytes hash are identical:

`4ff11078c8c3f003c32437d5f167a0241e510d6e6fa8c90088d7a6a7b19fcec9`

## Original Finding Dispositions

| Finding | Final disposition |
|---|---|
| Applicable protocol/history/mutation denominators and critical gate | `CLOSED` |
| One observation closes multiple obligations | `CLOSED` |
| Multi-range obligation closes from partial evidence | `CLOSED` |
| Generic rdma-core counted as explicit RoCE | `CLOSED` |
| Perftest explicit RoCE narrative lacks direct source entailment | `CLOSED` |
| Invalid dispositions inflate discovery recall | `CLOSED` |
| Shared evidence can ambiguously close multiple obligations | `CLOSED` |

## Commands and Evidence

- Breadth evaluator suite: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_quality_breadth_evaluator.py` from `backend/`: `47 passed in 0.04s`.
- Corpus loader suite: same controls on `tests/test_quality_benchmark_corpus.py`: `52 passed in 6.25s`.
- Pure Breadth corpus mutations for applicable dimensions and single ownership: `2 passed, 45 deselected in 0.11s`.
- Independent real-corpus mutation: all six proper subsets returned axis `fail`, protocol 0/1, and the exact protocol critical miss; all three refs returned axis `pass`, protocol 1/1, and no critical miss.
- Descriptor check: expected and actual SHA-256 both `4ff11078c8c3f003c32437d5f167a0241e510d6e6fa8c90088d7a6a7b19fcec9`.
- Exact owner scan: all three refs map only to `perftest-gid:protocol-roce-gid-selection`.

The formal all-truth perftest loader currently encounters the separately owned Depth `critical_chains` descriptor update. Per explicit review scope, that concurrent Depth fixture state is excluded from the Breadth verdict. The real Breadth case schema, universe descriptor, source bindings, mutations, and evaluator behavior were all checked directly.

## Verdict

`ACCEPT` for the explicit F012 Breadth implementation and corpus audit.

All previously confirmed Breadth findings are closed with independent green evidence. Real baseline distribution verification remains `pending_non_blocking`; it did not delay or determine this code/corpus verdict. Concurrent Depth fixture work is outside this verdict.
