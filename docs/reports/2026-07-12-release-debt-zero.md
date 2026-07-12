---
feature_ids: [release-debt-zero, F002]
topics: [release, validation, spdk, clowder]
doc_kind: release-report
created: 2026-07-12
---

# Release Debt-Zero Validation Report

## Scope

This release closes the accepted CodeTalk product and truth-source debts for multi-file delivery, staged builtin execution, executor safety/recovery, GitNexus capacity, Workbench maintainability and F002. Windows real-machine regression is the only explicit exclusion; Windows unit/contract coverage remains in the gate.

## Implemented

- AI thread delivery manifest v1 with safe relative paths, file-level schema status, producer, audience, size, sha256 and acceptance status.
- Individual file and ZIP downloads; long results render as a compact chat summary.
- Automatic dependent stages for comprehensive builtin-model test activities, preserving the exact original user request and prior accepted artifacts.
- Audited macOS/Linux Agent sandbox policy with explicit read/write/env/network boundaries and fail-closed required mode.
- Bounded GitNexus FIFO capacity, `Retry-After` support, capped backoff and user-visible queue/retry state.
- Workbench split into controller/shared/run/workflow/diagnostics/knowledge modules, all below 4,000 lines.
- F001 archived as superseded, F002 closed with live evidence, DeepWiki handoffs moved to a clearly marked historical archive.

## Real Browser Evidence

The exact same SPDK iSCSI Login task was entered through both products using mouse hover/click and real text input.

| Product | Runtime | Result |
| --- | --- | --- |
| Clowder AI | Native `3403/3404`, OpenCode `opencode/big-pickle` | 16 source/tool operations, 45 seconds, lifecycle and collapsed CLI process worked; final user body empty and no independent artifacts. |
| CodeTalk | Candidate `3503/3504`, builtin DeepSeek | Four automatic stages, 1 minute 23 seconds, 12 source evidence cards, compact final summary and three accepted independent deliverables. |

CodeTalk browser downloads contained:

- `artifact_manifest.json`
- `sfmea.json`: 15 rows
- `black_box_cases.json`: 10 cases covering all eight required dimensions
- `business_flow.md`

The downloaded ZIP contained 26,905 bytes across the manifest and three deliverables. All declared artifacts were accepted and had sha256 values. GPT source/test-path and boundary review scored the result 88/100.

GitNexus continuity was exercised through the browser by creating two detached SPDK workspaces back-to-back. Workspace A moved from indexing to indexed while workspace B remained visibly indexing, demonstrating serialized progress without duplicate completion or a surfaced 429; B then completed through the same queue.

## Regression Evidence

- Full backend suite: 2,062 passed, 8 skipped.
- Workbench real browser suite: 47 passed, using real hover, click, typing, drag/connect, execution and file downloads.
- Frontend contract suite: 40 passed; ESLint, TypeScript and the production Next build passed without warnings or errors.
- Deployer suite: 173 passed, 1 skipped.
- Tracked-file secret scan outside test fixtures was clean; no generated test, Playwright, Next or Python cache artifact is tracked.
- Node selection was repeated four times after the hydration gate fix; all four runs passed. The four failures found by the first full browser pass were rerun directly and then covered again by the green 47-test full pass.
- Independent review findings and their closure are recorded in `docs/bug-report/release-debt-review-findings/bug-report.md`.

## Remaining Risk

Windows real-machine spawn and OS-level isolation were not executed in this release by explicit scope decision. No other accepted item from the debt-zero objective is deferred.
