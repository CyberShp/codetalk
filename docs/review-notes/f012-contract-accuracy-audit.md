---
feature_ids: [F012]
topics: [independent-audit, evaluation-contract, accuracy, semantic-judge, corpus]
doc_kind: review-note
created: 2026-08-03
reviewer:
  id: R1-Codex
  identity: Codex independent reviewer
  model: GPT-5
  effort: high
scope: [AC1, AC2, AC10]
verdict: accept
baseline_output_verification: pending
full_quality_suite: failed_out_of_scope
---

# F012 R1 Contract And Accuracy Adversarial Audit

## Findings First

### Closed Finding: P1 - claim precision did not count every emitted factual claim

- **State:** `confirmed finding`
- **Disposition:** `closed` by independent R1 re-review on 2026-08-03.
- **Original evidence:** the prior implementation grouped all claims by canonical `semantic_key` and used the number of groups as the precision denominator. Two distinct emitted claim IDs with the same key returned `1/1`, violating AC2.
- **Fix evidence:** [quality_accuracy_evaluator.py](../../backend/app/services/quality_accuracy_evaluator.py) now retains a `_ClaimAssessment` per emitted claim (lines 82-107). `_claim_precision` flattens those assessments and uses `len(assessments)` as its denominator (lines 608-634). L1 similarly counts every assessment (lines 675-713), while L2 counts every emitted assessment plus each unique gold obligation (lines 716-755). Gold recall remains grouped and counts each gold obligation once (lines 637-655).
- **Adversarial re-test:** two supported observations sharing one exact `semantic_key` returned precision `2/2`, gold recall `1/1`, L1 `2/2`, and L2 `3/3`. A supported plus unsupported pair returned precision `1/2`, recall `0/1`, L1 `1/2`, L2 `1/3`, and both claim/gold critical misses. A supported plus contradicted pair returned precision `1/2`, recall `0/1`, L1 `2/2`, L2 `1/3`, `critical_contradiction`, and `critical_gold_contradicted`.
- **Why it matters:** F012 AC2 and the plan define claim precision as independently supported factual claims divided by **all factual claims emitted**. The prior grouping changed that denominator and hid the output volume that the score purported to measure; the new claim-level assessments restore the required definition.
- **Regression coverage:** [test_quality_accuracy_evaluator.py](../../backend/tests/test_quality_accuracy_evaluator.py) lines 219-322 covers multiple supported emitted facts mapped to one gold obligation, supported plus unsupported, supported plus contradicted critical, claim-level L1/L2 denominators, non-duplicated gold recall, and refusal to close gold recall for a conflicting group.

### Candidate Finding: P2 - live L2 paraphrase robustness is not yet evidenced

- **State:** `candidate finding`
- **Evidence:** the production semantic path delegates natural paraphrase evaluation to the isolated `gpt-5.5` judge in [quality_benchmark_semantic_judge.py](../../backend/app/services/quality_benchmark_semantic_judge.py) lines 208-419. The current tests cover five paraphrases through a deterministic fixture judge, and the request prompt explicitly requires a natural paraphrase to be accepted only when it fully entails the oracle (lines 530-544). No retained real isolated judge result exists for a harmless-context variant.
- **Disposition:** do not downgrade the fail-closed implementation to a defect. The frozen baseline must retain at least one real judge artifact for a natural paraphrase plus harmless context, including request/result hashes and model identity.

### Candidate Finding: P2 - positive applicability provenance is inconsistent across gold packages

- **State:** `candidate finding`
- **Evidence:** every one of the 57 gold claims declares `applicable`, but only the bmcweb, phosphor-nvme, and phosphor-state-manager packages include `applicability_evidence_refs`. The evaluator requires evidence when a claim is marked `not_applicable`, but not for a positive applicability declaration ([quality_accuracy_evaluator.py](../../backend/app/services/quality_accuracy_evaluator.py) lines 396-434).
- **Disposition:** Tier S case scope and source ranges are sufficient to keep this non-blocking today. Before enlarging conditional/E/H cases, require either a positive applicability reference or an explicit case precondition; otherwise an author cannot independently audit why the recall denominator includes a conditional fact.

### Ruled Out: P0 - aggregate-score or delivery-status injection

- **State:** `ruled out`
- **Evidence:** all contract models forbid extra fields ([quality_evaluation_contract.py](../../backend/app/services/quality_evaluation_contract.py) lines 74-76), and delivery status is recomputed from the final three-axis snapshot, hard failures, limitations, and terminal block state (lines 228-294). Direct mutations adding `aggregate_score` or `quality_score`, or forcing `delivery_status=ready`, each raised `ValidationError`. The aggregator only validates the frozen contract and does not compute a compensating score ([quality_evaluator.py](../../backend/app/services/quality_evaluator.py) lines 65-94).

### Ruled Out: P0 - benchmark truth can be labeled operational, or operational output can claim hidden-gold recall

- **State:** `ruled out`
- **Evidence:** an operational accuracy call with gold claims raises `operational scope forbids gold claims`; the report contract forbids a benchmark identity and `gold_recall` in operational scope, and requires both for `independent_benchmark` ([quality_evaluation_contract.py](../../backend/app/services/quality_evaluation_contract.py) lines 228-249). The read-only API retains the report scope and rejects an incompatible scope query ([quality_evaluations.py](../../backend/app/api/quality_evaluations.py) lines 42-79), while projection removes evidence refs and hidden miss identifiers (lines 82-120).

### Ruled Out: P0 - model self-agreement, unavailable judge, or expired judge can pass Accuracy

- **State:** `ruled out`
- **Evidence:** a non-destructive judge probe produced `non_independent` plus `SEMANTIC_JUDGE_NOT_INDEPENDENT` when generator and judge were both `gpt-5.5`; unavailable and expired-deadline probes returned only `insufficient` verdicts with their respective limitations. The production implementation independently verifies requested, validator, and response model identities ([quality_benchmark_semantic_judge.py](../../backend/app/services/quality_benchmark_semantic_judge.py) lines 256-419).

### Ruled Out: P0 - candidate self-reported L2, partial evidence, or a contradicted critical claim can pass

- **State:** `ruled out`
- **Evidence:** runner alignment overwrites candidate `l2_status`, requires evaluator-owned semantic verdicts, and maps only a single supported truth match ([quality_benchmark_runner.py](../../backend/app/services/quality_benchmark_runner.py) lines 942-1005). A wrong claim with a self-reported `supports` status became `contradicts`; a voice paraphrase became `supports` only after the evaluator path. Default multi-evidence policy requires every gold range, and explicit groups require one range from every group (lines 1008-1045). Direct probes showed one of two `all` references and one of two groups fail, while complete sets pass. Omitted critical gold, unsupported critical claim, and contradicted critical claim all produced axis failure and critical misses.

## Contract And Implementation Evidence

The strict v1 contract is versioned, frozen, and runtime-authoritative. It carries scope, benchmark identity, first/final snapshots, repair summary, per-axis status, L0-L3 outcomes, limitations, and hard failures. Required metrics include `claim_precision`; benchmark snapshots additionally require `gold_recall`. No aggregate metric exists in the enum or canonical report.

The runner evaluates first and final artifacts independently with the same absolute deadline and records semantic-judge audit metadata ([quality_benchmark_runner.py](../../backend/app/services/quality_benchmark_runner.py) lines 392-579). Generator output schema requires bounded candidate claims and their evidence; runner alignment, rather than generator-provided L2 values, is the only route to an accuracy semantic match. The API is read-only and redacts truth-derived evidence/miss IDs before a task-run projection is exposed.

## Gold Package Inspection

All 12 registered cases load through the corpus loader. I validated 112/112 gold evidence references with the production evidence-reference normalizer against the pinned source trees: every path existed and every line range was in bounds. The first critical source range for each case was also sampled directly.

| Project | Case | Tier | Gold / critical | Evidence refs | Sampled critical range |
|---|---|---:|---:|---:|---|
| bmcweb | `bmcweb-redfish-reset-action-info-001` | S | 6 / 5 | 6 | `redfish-core/lib/systems.hpp:L3552-L3561` |
| FEMU | `femu-bbssd-out-of-range-write-001` | S | 2 / 2 | 6 | `hw/femu/bbssd/bb.c:L85-L99` |
| LMCache | `lmcache-local-cpu-put-get-pinned-eviction-recovery-001` | S | 5 / 5 | 11 | `lmcache/v1/storage_backend/local_cpu_backend.py:L162-L170` |
| Mooncake | `mooncake-store-put-commit-readiness-recovery-001` | S | 5 / 5 | 18 | `mooncake-store/src/master_service.cpp:L3351-L3359` |
| nvme-csd | `nvme-csd-sync-compute-passthru-001` | S | 3 / 3 | 4 | `host/snia_cs_api/cs_api_nvme_tsp.c:L522-L524` |
| Open-CAS | `open-cas-wb-default-disconnect-001` | S | 3 / 3 | 4 | `casadm/cas_lib.c:L1171-L1198` |
| perftest | `perftest-roce-gid-selection-001` | S | 5 / 5 | 17 | `src/perftest_parameters.c:L992-L1013` |
| phosphor-nvme | `phosphor-nvme-present-power-not-good-001` | S | 7 / 5 | 9 | `nvme_manager.cpp:L662-L679` |
| phosphor-state-manager | `phosphor-state-hypervisor-boot-progress-001` | S | 7 / 6 | 7 | `hypervisor_state_manager.hpp:L42-L50` |
| rdma-core | `rdma-core-mw-remote-access-denied-001` | S | 6 / 6 | 14 | `tests/test_qpex.py:L137-L144` |
| SPDK | `spdk-concurrent-bdev-reset-001` | S | 3 / 3 | 3 | `module/bdev/nvme/bdev_nvme.c:L3162-L3182` |
| UCX | `ucx-roce-reachability-mode-001` | S | 5 / 5 | 13 | `src/uct/ib/base/ib_iface.c:L70-L75` |

The corpus currently has 57 gold claims, 53 marked critical, all marked applicable, and the requested storage/BMC/KV/RDMA/RoCE project coverage is represented. This validates source-range closure and structural applicability; it does not replace the pending real baseline and dual-review artifacts.

## Commands And Results

```text
cd /Volumes/Media/codetalk-quality-eval-baseline/backend
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_quality*.py
# 440 passed in 18.86s

PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_quality_evaluation_contract.py \
  tests/test_quality_accuracy_evaluator.py \
  tests/test_quality_evaluator.py \
  tests/test_quality_benchmark_runner.py \
  tests/test_quality_benchmark_semantic_judge.py \
  tests/test_quality_evaluations_api.py
# 152 passed in 0.57s

cd /Volumes/Media/codetalk-quality-eval-baseline
PYTHONPATH=backend backend/.venv/bin/python <temporary adversarial probes>
# omitted gold / unsupported critical / contradicted critical: fail
# all/groups partial evidence: reject; complete evidence: accept
# same model / unavailable / deadline judge: insufficient and limited
# aggregate and delivery injection: ValidationError
# two claims sharing one semantic key: claim_precision=1/1 (confirmed P1)
```

`git diff --check` passed. Probes used temporary directories or in-memory payloads only; no implementation, test, corpus, or external source file was modified.

### R1 Re-review After RED To GREEN

```text
cd /Volumes/Media/codetalk-quality-eval-baseline
PYTHONPATH=backend backend/.venv/bin/python <original same-key exploit plus mixed-group probes>
# two supported same key:
#   precision=2/2, gold_recall=1/1, L1=2/2, L2=3/3, axis=pass
# supported + unsupported:
#   precision=1/2, gold_recall=0/1, L1=1/2, L2=1/3, axis=fail
# supported + contradicted critical:
#   precision=1/2, gold_recall=0/1, L1=2/2, L2=1/3, axis=fail

cd backend
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_quality_accuracy_evaluator.py \
  tests/test_quality_evaluation_contract.py \
  tests/test_quality_evaluator.py \
  tests/test_quality_benchmark_runner.py \
  tests/test_quality_benchmark_semantic_judge.py \
  tests/test_quality_evaluations_api.py
# 154 passed in 0.82s

PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_quality*.py
# 445 passed, 2 failed in 20.60s
# Both failures are outside R1 Contract/Accuracy scope:
# - phosphor-nvme is missing the expected Breadth mutation dimension
# - the phosphor-nvme positive dynamic fixture consequently fails Breadth
# Accuracy passes in that dynamic fixture.
```

The two full-suite failures were reproduced independently as `2 failed in 0.17s`. They do not reopen the Accuracy P1 and are not classified by R1 as new Contract/Accuracy P0/P1 findings. They do block any claim that the overall F012 release test gate is green and require disposition by the owning Breadth/corpus review.

## AC Disposition

| Criterion | Status | Basis |
|---|---|---|
| AC1 contract | accept in R1 code scope | Strict schema, scope separation, three-axis conjunction, aggregate injection controls, and claim-level Accuracy metric accounting passed. |
| AC2 accuracy | accept in R1 code scope | Claim precision now counts every emitted factual observation; gold recall remains independently deduplicated and refuses conflicting groups. Critical omission, unsupported evidence, contradiction, L1, and L2 gates passed adversarial re-test. |
| AC10 independent audit | accept for R1 | This reviewer did not author the fix, reproduced the original exploit, tested mixed/conflicting groups, and retained commands and evidence. Other independent audits and the final Vision/merge gates remain separate. |

## Verdict

**R1 Contract and Accuracy code-audit verdict: ACCEPT.** The original P1 is closed and no new confirmed P0/P1 was found in R1 scope. Real baseline-judge output verification remains explicitly pending and is not approved by this audit. The overall F012 release gate is also not approved while the two recorded out-of-scope Breadth/corpus tests remain red.
