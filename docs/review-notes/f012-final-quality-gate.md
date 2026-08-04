---
feature_ids: [F012]
topics: [quality-gate, verification, formal-baseline, release-block]
doc_kind: review-note
created: 2026-08-04
---

# F012 Final Quality Gate

## Vision Check

| Original requirement | Evidence | Disposition |
|---|---|---|
| Accuracy, Breadth, and Depth must be independently verified | strict three-axis reports, hidden truth, independent axis audits | implemented |
| Terminal block is a fallback, not the normal interaction | bounded automatic repair, no retry while repairing, terminal-only retry E2E | implemented |
| Cover storage card/controller, BMC, KV Cache, RDMA, and RoCE projects | pinned 12-project corpus and 12/12 formal attempts | implemented |
| Rapid <=15 minutes; deep <=90 minutes | core rapid p100 `462.809s`; paired deep p100 `872.874s` | pass |
| Treat under-five runs with suspicion, not an automatic failure | two cold samples independently audited; work-sufficiency gate pass | pass |
| Establish a quality baseline and release thresholds | only 10/12 cases evaluable | blocked |

The resulting system can be extended with new cases and truth packages without
rewriting its contracts. The incomplete baseline is not presented as a partial
feature release or as frozen numeric policy.

## Close Gate Matrix

| Unmet acceptance item | Disposition | Reason |
|---|---|---|
| AC9 complete calibration and frozen thresholds | blocked | Mooncake and SPDK lack evaluable reports |
| AC9 alternative-model/historical regression sample | blocked | no accepted prior baseline and no retained alternative-model sample |
| AC10 release flow | blocked | merge gate cannot pass while AC9 is incomplete |

No unmet item is silently deferred, deleted, or represented as accepted.

## Formal Evidence

- Bundle: `/Volumes/Media/codetalk-quality-evidence/f012-baseline-blocked-c193eb2c`
- Manifest SHA-256: `0e1c49ac9631cfc1530afd81244a0263807445ac2c6950eb0200af24d1daea2d`
- Coverage: 12 attempted, 10 evaluated, 2 generation-blocked
- Release reasons: `generation_failures_present`, `thresholds_not_frozen`,
  `repair_attempt_audit_unavailable`
- R4 bundle audit: `ACCEPT`
- R5 implemented scope: `ACCEPT WITH P2 COVERAGE LIMITATIONS`
- R5 release and goal: `BLOCKED`

## Fresh Verification

| Command or check | Result |
|---|---|
| Focused freezer + generator pytest | `93 passed in 38.82s` |
| Complete F012 quality pytest | `664 passed in 50.43s` |
| Complete backend pytest | `4763 passed, 69 failed, 8 skipped` |
| Backend parity | all 69 failures shared with old F012 and pre-F012 base; zero new failures |
| Frontend `npm run lint` | exit 0 |
| Frontend `npm run build` | exit 0 |
| Isolated Playwright on worktree ports 3103/3104 | `13 passed, 1 skipped` |
| Formal bundle recomputation | 330/330 artifacts and all identities matched |
| `git diff --check` | clean |
| F012 `.pen` lookup | no matching design file |
| Root media/design artifact hygiene | no findings |
| Hotfix/fallback-layer repository scripts | not present in this repository |

The Playwright run used
`CODETALK_BACKEND_PYTHON=/Volumes/Media/codetalk-quality-eval-baseline/backend/.venv/bin/python`,
`CODETALK_FRONTEND_PORT=3103`, `CODETALK_BACKEND_PORT=3104`, and
`CODETALK_REUSE_EXISTING_SERVER=0`. The screenshots therefore came from the
current worktree, not a service on the public runtime ports.

Desktop ready at 1440x900, desktop blocked at 1280x800, and mobile repairing at
390x844 were visually inspected. The three-axis panel, terminal retry, repair
progress, status labels, and controls do not overlap or truncate. The mobile
layout places the repair state before lower-priority run detail.

## Review Disposition

The final review findings were handled through the receive-review discipline:

- R4's implementation and evidence-integrity P1/P2 set is empty after the
  freezer-contract fixes; R4 independently confirmed the final bundle.
- R5's three release blockers and P2 coverage limitations were reproduced
  against the frozen evidence. They are evidence gaps, not code defects that
  can be reconstructed after the run, so no speculative patch was applied.

The request-review gate was not re-issued after this quality gate returned
`BLOCKED`: its prerequisites require a green quality gate. The merge gate also
stops before PR creation because AC9 is incomplete, the repository-wide test
gate is red, and no reviewer approval can extend to a not-yet-created final
documentation commit. The branch may still be committed and pushed as the
user-requested blocked checkpoint; it must not be represented as merge-ready.

## Gate Result

**BLOCKED.** The implementation-specific tests, frontend checks, E2E, evidence
integrity, and independent reviews pass. F012 cannot be declared complete or
merged as a released baseline because AC9 lacks 12/12 evaluable evidence and
frozen thresholds. The repository-wide backend suite also retains 69 inherited
failures, with no new failure relative to either comparison checkpoint.
