---
feature_ids:
  - workbench-v2
topics:
  - independent-review
  - release
doc_kind: review-request
created: 2026-07-13
---

# Workbench V2 Independent Review Request

## What

Review the complete diff from `origin/feat` at `a38e8840` through the current working tree on
`codex/workbench-v2`, including all Phase 0-7 commits and uncommitted Phase 8 changes. Inspect source,
tests, migrations, compatibility behavior, user flows, and release documentation. Do not edit files.

## Why

The Goal may complete only after an independent reviewer confirms that the implementation satisfies
the supplied Workbench V2 plan and reports no unresolved P0, P1, or P2 defects.

## Tradeoff

Phase 8 keeps the legacy UI/controller behind a server-owned rollback switch for one release instead
of deleting it. Active V2 code is split by domain, while old APIs and exports remain compatible.

## Open questions

- Can concurrent startup or migration bypass/corrupt the verified SQLite backup boundary?
- Can API base fallback leak requests across local runtimes or regress custom deployments?
- Does tail/backward event pagination omit, duplicate, or reorder events around the SSE boundary?
- Can a route or client bypass the server-owned release switch or lose legacy data access?
- Are Workflow Graph, effective Task overrides, frozen plans, retries, artifacts, and the three
  outcome states internally consistent under failures and historical-data compatibility?
- Do any UI controls imply behavior the backend does not actually support?
- Is any sensitive value exposed through public APIs, events, logs, artifacts, or documentation?

## Next action

Return findings first, ordered P0/P1/P2/P3, with absolute file path and tight line references plus a
concrete failure scenario. Distinguish required fixes from residual deployment risk. If no P0/P1/P2
exists, say so explicitly and list any remaining test gap. Do not approve based only on test counts.
