---
feature_ids:
  - workbench-v2
topics:
  - workbench
  - workflow
  - migration
doc_kind: implementation-progress
created: 2026-07-13
---

# Workbench V2 Implementation Progress

## Baseline

- Base commit: `a38e884033dc3d18715e051097af351cb0e7ec3a`
- Worktree: `/Volumes/Media/codetalk-workbench-v2`
- Branch: `codex/workbench-v2`
- Verification ports: frontend `3123`, API `3124`, Redis `6398` only.
- Release boundary: local phase commits only; no push, PR, merge, or remote branch changes.

## Phase 0 - Baseline And Characterization

### Before implementation

- Goal: freeze legacy workflow persistence, task-run recovery/events, semantic import/search,
  and public response shapes before introducing V2 stores and routes.
- Expected files: `backend/app/config.py`, backend characterization tests, API contract fixture,
  this progress log, and the architecture decision log.
- Migration impact: none. Phase 0 must not mutate an existing database schema.
- Compatibility strategy: `WORKBENCH_V2_ENABLED` defaults to false; every existing route and
  response remains authoritative while V2 is disabled.
- Test plan: verify flag default/env parsing; mutable legacy save plus frozen run snapshot;
  task-run list/load/event pagination/restart reconciliation; semantic JSON import and FTS
  retrieval; run existing backend Workbench tests, frontend contract tests, and Playwright smoke.

### Result

- Added a dark-by-default `WORKBENCH_V2_ENABLED` setting with environment parsing.
- Captured legacy public API shapes in `docs/contracts/workbench-v1-api-samples.json`.
- Added characterization coverage for mutable legacy workflows and detached snapshots, task-run
  list/load/events/restart reconciliation, and semantic JSON import plus FTS retrieval.
- Fixed the pre-existing cross-platform command resolver to use `PureWindowsPath`; the previous
  platform-dependent `Path` crashed the Windows PATHEXT test on macOS before pytest could report it.
- Verification:
  - Focused backend regression: `124 passed`.
  - Frontend ESLint: passed.
  - Frontend TypeScript and production build: passed.
  - Existing Workbench and health Playwright suite on ports 3123/3124: `21 passed`.
  - Expanded backend run reached `933 passed, 7 skipped` with no failure after 10 minutes; the
    complete long-running suite is reserved for the final release gate to avoid repeating it for
    all nine phases.
- Known limitation: V2 remains dark and no new schema or route is present by design.
- Next dependency: Phase 1 can rely on frozen legacy read/write and snapshot contracts while adding
  a separate idempotent version store and compatibility adapter.
