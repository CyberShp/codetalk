---
feature_ids: [F012]
topics: [quality-evaluation, product-ui, independent-audit, vision-guardian]
doc_kind: review-note
created: 2026-08-03
reviewer_identity: "F012 R5 Independent UI Auditor"
reviewer_model: "GPT-5 (Codex)"
reviewer_effort: ultra
independence: "Reviewer did not author the F012 UI, API projection, or Playwright fixture."
reviewed_head: bca88474394cacda5fdc4873f62df8014f7ee3d8
reviewed_worktree: dirty
re_reviewed: 2026-08-03
ui_verdict: ACCEPT
vision_guardian_verdict: pending_baseline
---

# F012 R5 UI Audit And Vision Gate

## Current UI Verdict

**ACCEPT for F012 code/UI scope.** The previous generic-ordinal P1 is closed. The
public API now emits stable, per-run, truth-safe labels that distinguish a failed
generated claim, a missed Breadth obligation, and an open Depth node without exposing
hidden truth ids, semantic answers, or source references. The existing run cockpit
renders those labels progressively, keeps Accuracy/Breadth/Depth independent, and
retains repair-before-retry behavior at all required viewports.

The separate Vision Guardian verdict is deliberately **`pending_baseline`**. It must
be revisited only after a formally frozen, real baseline and the associated audit
evidence are available. That pending completion does not downgrade the code/UI verdict
or AC8 disposition, but this review still does not approve the overall F012 release.

## Findings First

### [P1][closed finding] Axis expansion now identifies truth-safe failed obligations

AC8 requires progressive disclosure of the exact failed claims, missed universe
items, and open chain nodes ([F012 feature](../features/F012-quality-evaluation-baseline.md#product-integration), AC8). The UI brief makes those three categories the
primary action after scanning the axes ([implementation plan](../plans/2026-08-03-f012-quality-evaluation-baseline.md#product-ui-brief)).

The API derives a stable public id from `run_ref`, axis, and the private item id, then
emits an axis-specific public label
([quality_evaluations.py](../../backend/app/api/quality_evaluations.py#L98-L150),
[identity mapping](../../backend/app/api/quality_evaluations.py#L157-L203)). Accuracy
may expose a syntactically bounded generated claim id because that id belongs to the
public candidate. Hidden gold omissions, Breadth items, and Depth nodes/edges/checks
receive distinct `REF-<digest>` aliases. The projection continues to remove original
miss ids, private reasons, `evidence_refs`, and `truth://` content.

Independent API probes verify that aliases are stable for the same run, distinct for
different misses, categorized by axis, and do not contain hidden ids or source refs
([test_quality_evaluations_api.py](../../backend/tests/test_quality_evaluations_api.py#L99-L214)). The UI fixture now expands all three axes and asserts concrete labels:
`生成事实 CLAIM-reset-42`, `协议覆盖项 REF-BREADTH01`, and
`因果链节点 REF-DEPTH0001`
([v3-quality-evaluation-cockpit-real.spec.ts](../../frontend/e2e/v3-quality-evaluation-cockpit-real.spec.ts#L39-L54)). Separate v3 screenshots retain each expanded state.

**Disposition:** `closed`. The labels are specific enough to distinguish and target
the public obligation while remaining deliberately non-answer-bearing. No raw truth
id, source range, or hidden semantic statement is exposed.

### [P2][closed finding] Operational scope wording is now explicit

The type contract distinguishes `operational` from `independent_benchmark`, and the
operational report correctly omits `gold_recall`. The rendered distinction is now
`运行内质量审计` versus `独立基准评估`
([quality-evaluation-panel.tsx](../../frontend/src/features/runs/quality-evaluation-panel.tsx#L40-L42)), and the operational E2E asserts both the label and absence of benchmark-only recall. **Disposition:** `closed`.

### [P2][closed finding] F012 frontend validation is reproducible

The stale `.next-playwright-audit/` directory from the first review is no longer
present. Targeted lint over all changed F012 UI and E2E files passed, the production
build passed, and `git diff --check` passed. A later unrestricted `npm run lint` was
stopped because it recursively entered an untracked temporary Next build directory;
that directory was moved out of the worktree. This is repository hygiene outside the
F012 code verdict, not an open product finding.

## Verified Contract Behavior

| Check | Result | Evidence |
|---|---|---|
| Existing cockpit rather than a new dashboard | Pass | The panel is rendered inside `RunCockpitPage`; no route, navigation item, or dashboard was added ([run-cockpit-page.tsx](../../frontend/src/features/runs/run-cockpit-page.tsx#L407-L414)). |
| No aggregate masks Accuracy/Breadth/Depth | Pass | Three fixed rows are rendered independently; no combined score is displayed ([quality-evaluation-panel.tsx](../../frontend/src/features/runs/quality-evaluation-panel.tsx#L35-L46)). |
| Operational and benchmark reports differ | Pass | The fixture covers both scopes, uses the explicit `运行内质量审计` label, and operational output omits `gold_recall` ([v3-quality-evaluation-cockpit-real.spec.ts](../../frontend/e2e/v3-quality-evaluation-cockpit-real.spec.ts#L75-L92)). |
| Repairing does not offer a manual retry | Pass | The terminal retry control is guarded by `terminalBlocked`, while repairing only shows bounded automatic progress ([quality-evaluation-panel.tsx](../../frontend/src/features/runs/quality-evaluation-panel.tsx#L24-L43)). The fixture asserts no retry in repairing state. |
| Retry is available only after terminal quality block | Pass | The blocked fixture sees exactly one `重新运行质量修复`; ready, limited, repaired, and repairing states do not expose it. |
| Ordinary execution failure retry remains available | Pass | Retry suppression applies only after a report is `ready` or while automatic repair is active ([run-cockpit-page.tsx](../../frontend/src/features/runs/run-cockpit-page.tsx#L251-L259)); the real Cockpit E2E reaches and executes `从失败节点重试`. |
| Limitations, repair comparison, and misses are progressive | Pass | Collapsed `details` and axis buttons disclose L3 limitations, first/final comparison, and the three distinct public obligations only on demand. |
| 1440x900, 1280x800, and 390x844 layout | Pass | Reviewed ready, blocked, repairing, and three expanded-axis screenshots below. No overlap, truncation, hidden retry, or horizontal overflow was observed. |

## Screenshot Evidence

Reviewed the v3 capture set produced by the passing fixture:

- [Ready, 1440x900](/Volumes/Media/codetalk-quality-ui-evidence/f012-2026-08-03-v3/f012-ready-1440x900.png): separate axes, benchmark scope, and no aggregate.
- [Blocked, 1280x800](/Volumes/Media/codetalk-quality-ui-evidence/f012-2026-08-03-v3/f012-blocked-1280x800.png): terminal reason and the only quality retry control.
- [Repairing, 390x844](/Volumes/Media/codetalk-quality-ui-evidence/f012-2026-08-03-v3/f012-repairing-390x844.png): automatic-repair attempt progress is visible before workflow detail and has no manual retry.
- [Accuracy expanded](/Volumes/Media/codetalk-quality-ui-evidence/f012-2026-08-03-v3/f012-blocked-accuracy-1280x800.png): `生成事实 CLAIM-reset-42` is readable and remains inside the panel.
- [Breadth expanded](/Volumes/Media/codetalk-quality-ui-evidence/f012-2026-08-03-v3/f012-blocked-breadth-1280x800.png): `协议覆盖项 REF-BREADTH01` is readable and distinct.
- [Depth expanded](/Volumes/Media/codetalk-quality-ui-evidence/f012-2026-08-03-v3/f012-blocked-depth-1280x800.png): `因果链节点 REF-DEPTH0001` is readable and distinct.

No overlapping controls, clipped primary action, or separate quality dashboard was
observed. The compact result section uses semantic buttons and native `details`, and
the latest Web Interface Guidelines review found no new F012-specific accessibility
or interaction P0/P1. The substantial blank area in the ready desktop capture is inherited
cockpit workspace space, not a F012-only container; it is not classified as a P0/P1
finding in this review.

## Commands And Results

```text
cd /Volumes/Media/codetalk-quality-eval-baseline/frontend
npx eslint src/features/runs/quality-evaluation-panel.tsx \
  src/features/runs/run-cockpit-page.tsx src/lib/types.ts \
  src/lib/api/quality-evaluations.ts \
  e2e/v3-quality-evaluation-cockpit-real.spec.ts \
  e2e/v3-quality-audit-preflight-cockpit-real.spec.ts \
  e2e/workbench-v2-run-cockpit-real.spec.ts
# pass

CODETALK_NEXT_DIST_DIR=/tmp/codetalk-f012-r5-recheck-next npm run build
# pass

cd /Volumes/Media/codetalk-quality-eval-baseline/backend
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_quality_evaluations_api.py
# 5 passed in 0.09s

cd /Volumes/Media/codetalk-quality-eval-baseline
git diff --check
# pass

cd frontend
CODETALK_FRONTEND_PORT=3103 CODETALK_BACKEND_PORT=3104 \
CODETALK_BACKEND_PYTHON=/Volumes/Media/codetalk-quality-eval-baseline/backend/.venv/bin/python \
CODETALK_REUSE_EXISTING_SERVER=0 CODETALK_NEXT_DIST_DIR=/tmp/codetalk-f012-r5-playwright-next \
npx playwright test e2e/v3-quality-retry-real.spec.ts \
  e2e/v3-quality-audit-preflight-cockpit-real.spec.ts \
  e2e/v3-quality-evaluation-cockpit-real.spec.ts \
  e2e/workbench-v2-run-cockpit-real.spec.ts --project=chromium
# 13 passed, 1 skipped in 33.6s
```

The passing suite includes all six quality states, pending polling, operational scope,
hidden-truth absence, all three viewport captures, the real independent-quality
preflight, and the ordinary execution-failure retry path. The sole skip is the
persisted-parent retry test, which requires externally supplied run ids and is not a
code/UI failure.

## Vision Guardian Handoff

The next R5 pass must consume a frozen baseline root, immutable manifest SHA,
per-axis reports, calibration and under-five-minute work-sufficiency audits. It must
then re-evaluate the full product contract: repair-before-block, no aggregate masking,
operational versus independent
scope, truth isolation, all-domain coverage, and time semantics.

**Code/UI verdict: `ACCEPT`. Vision Guardian completion: `pending_baseline`.**
