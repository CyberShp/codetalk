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

## Phase 1 - Workflow Version Store And Migration

### Before implementation

- Goal: add workflow headers and immutable versions, migrate each legacy definition to Published
  V1, expose draft/version lifecycle APIs, and preserve legacy execution reads.
- Expected files: a version-store service, a focused V2 workflow router, the existing Workbench
  router compatibility dispatch, application router registration, migration/API tests, and docs.
- Migration impact: add `workbench_schema_meta`, `workflow_headers`, and `workflow_versions` to the
  existing `workflows.db`; copy legacy rows transactionally and never delete or rewrite the old table.
- Compatibility strategy: legacy rows remain the execution source while V2 is dark. When enabled,
  compatibility responses resolve the published compiled definition and continue exposing legacy
  `inputs/steps/outputs`; existing snapshots and run artifacts are untouched.
- Test plan: idempotent old-row migration; one-current-draft invariant; draft updates; published
  immutability; monotonically increasing published versions; archive behavior; V2 API error/status
  contracts; existing workflow CRUD and task-run preparation regression.

### Result

- Added `workbench_schema_meta`, `workflow_headers`, and `workflow_versions` with an idempotent,
  transactional migration in the existing `workflows.db`.
- Legacy definitions migrate once to immutable Published V1 records; the original table and exact
  compiled definition remain intact, and migration is safe to rerun.
- Added workflow header/draft/version lifecycle storage, one-current-draft enforcement, published
  immutability, monotonic published numbers, archive, compatibility definition reads, and strict
  identifier validation for newly authored workflows.
- Added V2 version lifecycle routes plus feature-flagged creation through the existing workflow
  endpoint. Existing DSL requests are unchanged while V2 is dark.
- With V2 enabled, legacy list/detail routes merge workflow header metadata and authoring graph data
  while retaining `inputs/steps/outputs` for old clients and task-run preparation.
- Application startup runs the migration only when V2 is enabled and fails visibly on migration
  errors instead of continuing with a partial schema.
- Verification:
  - New store, migration, immutability, and API tests: `5 passed`.
  - Workflow API/preset/foundation regression: `34 passed`.
  - Phase 0 compatibility subset: passed.
  - Frontend ESLint: passed.
- Known limitation: Phase 1 publishing performs server-side legacy DSL validation. Authoring Graph
  V2 validation and deterministic compilation replace this bridge in Phase 2.
- Next dependency: Phase 2 consumes immutable draft graphs and persists its validation result,
  compiled definition, and execution plan on the draft before publication.
