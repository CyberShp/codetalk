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

## Phase 2 - Graph Validator, Compiler, And Scheduler

### Before implementation

- Goal: define Authoring Graph V2, validate all publish invariants, deterministically compile legacy
  Workflow Definition plus a real Execution Plan, and execute strictly from DAG dependencies.
- Expected files: graph types/validator/compiler, DAG scheduler, workflow version compile/publish API,
  task-run preparation/runner/event compatibility adapters, focused backend tests, and docs.
- Migration impact: no new tables. Draft rows gain persisted validation, compiled definition, and
  compiled plan JSON; migrated Published V1 definitions remain immutable.
- Compatibility strategy: a legacy compiler emits a sequential dependency plan matching historic
  array execution. V2 runs carry their compiled plan in the frozen task bundle; old task runs without
  one continue through the legacy adapter.
- Test plan: duplicate IDs, missing refs/ports, type mismatch, cycles, provider/MCP/skill capability
  checks, unsafe/duplicate artifacts, reachability, deterministic compile, shuffled node arrays,
  direct-dependency inputs, independent branch continuation, blocked downstream nodes, legacy plan,
  and public `agent_output` event mapping.

### Result

- Added Authoring Graph V2 validation for IDs, supported node/edge kinds, node/port references,
  port types, cycles, required input/output/port bindings, Agent goals, provider availability,
  MCP compatibility, unknown skills, retry/failure policies, artifact safety/uniqueness/mapping,
  and orphan reachability.
- Added deterministic compilation to legacy Workflow Definition and Execution Plan. Stable sorting
  makes shuffled node/edge arrays produce identical output, while version metadata is embedded in
  both compiled forms.
- Added an explicit legacy compiler that preserves historical sequential execution without editing
  migrated definitions.
- Added a serial DAG scheduler with direct-dependency validated outputs, explicit queued/running/
  completed/failed/blocked states, downstream blocking, independent branch continuation, and
  stop-policy handling.
- Integrated frozen compiled plans into the real Workbench runner. Plan-driven runs execute by DAG,
  inject only direct validated dependency outputs, emit both new node events and compatible step
  events, and persist blocked results in the normal execution artifact.
- Added public event mappings for `agent_output`, node lifecycle, quality lifecycle, and run completion.
- Added server-side validate/compile/publish routes. Publish always recompiles the stored graph with
  the current capability matrix and never trusts client validation or compiled payloads.
- Verification:
  - Graph/compiler/scheduler/version API tests: `14 passed`.
  - Workbench task-run regression: `94 passed`.
  - Workflow API/foundation/preset regression: `36 passed`.
- Known limitation: execution is intentionally serial (`max_parallelism = 1`). Capability discovery
  currently includes built-in LLM plus configured external provider specs; the Phase 3 inspector
  consumes the same API surface.
- Next dependency: Phase 3 can use validation issues for canvas focus, compiled plan preview for the
  review step, and immutable version APIs for draft auto-save and publication.

## Phase 3 - Workflow Library, Wizard, And Canvas Designer

### Before implementation

- Goal: replace the legacy JSON-first authoring surface with a workflow library, a six-step guided
  creation flow, and a direct-manipulation canvas while keeping server validation and compilation
  authoritative.
- Expected files: typed frontend workflow client and graph model, library/version routes, wizard,
  canvas, inspector, trial-run panel, focused V2 styles, capability discovery, and real E2E coverage.
- Migration impact: no new schema. Draft auto-save, compile results, and publication use the Phase 1
  version store; trial runs freeze the Phase 2 compiled plan into the existing task-run artifact.
- Compatibility strategy: legacy Workbench routes remain available. The new `/workflows` routes are
  feature-flagged and consume existing workspace, runtime-provider, upload, execution, and artifact
  APIs instead of introducing independent copies.
- Test plan: create and publish through the browser; drag nodes with the mouse; connect and delete
  edges directly; restore a draft; validate and compile server-side; select a real workspace and
  start execution; verify production build, desktop containment, and legacy backend regressions.

### Result

- Added a compact workflow library with search/status filters, archive/edit/version actions, guided
  creation, immutable version history, and a read-only compiled-plan detail view.
- Added a six-step wizard for metadata, named inputs, Agent/provider/Skills/MCP selection, named
  outputs, graph arrangement, validation, trial run, and publication. Normal users never edit JSON.
- Added a direct-manipulation canvas with node-library drag/drop, free node movement, canvas pan,
  zoom/fit, direct port-to-port connections, edge selection/deletion, keyboard deletion, and a
  50-operation undo/redo history.
- Added a contextual inspector with form controls and searchable Skills/MCP choices. Provider options
  are sourced from configured runtimes and identify duplicate display names by stable provider ID.
- Drafts auto-save after 800 ms; validation, compilation, trial preparation, and publication always
  use the stored server graph. Published versions are immutable and new edits create the next draft.
- Added a real trial-run adapter that resolves the selected workspace on the server, injects every
  `resolver: workspace` input, ingests uploaded files through the existing service, freezes the
  compiled plan, and starts the existing execution endpoint. Invalid inputs return actionable 422
  responses instead of leaking internal exceptions.
- Real browser verification created and indexed a workspace, completed all wizard steps, dragged a
  node, deleted and reconnected an edge through its ports, validated, compiled, started a real task,
  published V1, created V2, and restored it in the full designer.
- Verification:
  - Workflow version/trial backend tests: `9 passed`.
  - Real no-mock workflow browser E2E: `1 passed`.
  - Frontend ESLint: passed with zero warnings.
  - Frontend TypeScript and production build: passed; all V2 dynamic routes generated.
  - Desktop containment at `1440x900`: document width equals viewport width; no horizontal overflow.
  - Screenshot: `output/playwright/phase3/workflow-designer-trial.png` (ignored runtime evidence).
- Known limitation: trial execution links into the legacy run cockpit because the Task/Attempt model
  and V2 run-detail surface are Phase 4 and Phase 5 work. Canvas touch/mobile ergonomics remain a
  final Phase 8 validation item; desktop mouse authoring is the supported Phase 3 path.
- Next dependency: Phase 4 can associate immutable workflow versions and prepared task runs with a
  first-class Task plus Attempt history without changing this authoring contract.
