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

## Phase 4 - Task Model, Attempts, And Task Center

### Before implementation

- Goal: separate reusable user Tasks from immutable Run Attempts, add task lifecycle management and
  filtering, and introduce task list/detail routes while preserving all historical task-run access.
- Expected files: a task SQLite store/migration, a focused task API router, compatible task-run
  metadata helpers, backend migration/API tests, typed frontend task client, `/tasks` list and
  `/tasks/[taskId]` detail pages, navigation changes, and a real browser contract test.
- Migration impact: add `workbench_tasks` and a schema metadata entry in the existing Workbench
  database. Existing artifact directories are not rewritten; new run snapshots gain optional
  `task_id`, `attempt_number`, `parent_task_run_id`, and separated execution/quality/delivery states.
- Compatibility strategy: runs without `task_id` remain visible through a read-only historical-runs
  API and the legacy run route. Existing task-run JSON deserialization supplies safe defaults for
  all new optional fields. Task archive is soft-delete and cannot remove running attempts.
- Test plan: idempotent migration; CRUD/filter/archive/clone; fixed workflow version; attempt number
  monotonicity; one Task with multiple attempts; legacy-run read compatibility; URL-synchronized
  task filters; list/detail browser rendering; existing task-run and workflow regressions.

### Result

- Added the exact `workbench_tasks` Task model with idempotent schema metadata, JSON input/config/
  output snapshots, tags, soft archive, fixed workspace/workflow/version identity, and last-run link.
- Added Task create/read/update/archive/clone APIs plus keyword, lifecycle, execution, quality,
  workflow, workspace, date, and pagination filters. Ready tasks are checked against required inputs;
  normal tasks can reference only a Published Workflow Version.
- Extended existing run artifacts with backward-compatible Task/Attempt metadata and separate
  execution, quality, and delivery states. Legacy JSON loads with safe defaults and is never rewritten
  merely to appear in the read-only historical-runs endpoint.
- Added monotonic Attempt creation for each Task, optional parent-run lineage, server workspace input
  resolution, frozen workflow version/compiled plan/task overrides, and `last_run_id` updates. Starting
  a run from Task detail uses the existing execution endpoint rather than a parallel runner.
- Event status transitions now synchronize the public `execution_status`, `started_at`, and
  `completed_at` fields while preserving the existing `status` and `runtime` compatibility fields.
- Added `/tasks` with URL-backed filters, bounded table scrolling, workflow/workspace labels, archive,
  clone, and a bounded old-run panel. Added `/tasks/task_*` details with overview, Attempt history,
  inputs, execution configuration, outputs, and activity tabs.
- Preserved legacy UUID task pages by routing only the new `task_` IDs to the V2 detail component;
  existing report/export routes and legacy task behavior remain intact.
- Verification:
  - Task store, migration, API, association, archive guard, and legacy compatibility: `5 passed`.
  - Expanded task-run, event recovery, characterization, and DAG regression: `94 passed`.
  - Real no-mock task-center browser E2E: `1 passed` after its locator ambiguity was corrected.
  - Frontend ESLint: passed with zero warnings.
  - Frontend TypeScript and production build: passed; `/tasks` and `/tasks/[id]` generated.
  - Real browser created Attempt 3 from a two-Attempt Task and showed all three without overwrite.
  - Desktop containment at `1440x900`: no horizontal overflow; list uses an internal scroll boundary.
  - Screenshot: `output/playwright/phase4/task-attempt-history.png` (ignored runtime evidence).
- Known limitation: task creation currently uses the Phase 4 API and Task detail defaults. The normal
  six-step creation/configuration experience, inherit/replace overrides, output customization, draft
  recovery, and final compile are intentionally deferred to Phase 5.
- Next dependency: Phase 5 consumes the immutable Task store and published workflow contracts to
  implement the six-step task wizard without changing Task/Attempt identity or old run artifacts.

## Phase 5 - Six-Step Task Wizard And Effective Configuration

### Before implementation

- Goal: let normal users create a Task from a Published Workflow Version through six bounded steps,
  while advanced users can explicitly replace per-node execution resources and customize allowed
  outputs without mutating the workflow version.
- Expected files: a task effective-configuration compiler/validator, Task compile API and run adapter,
  override/output unit and API tests, six-step `/tasks/new` UI, dynamic input/upload controls, draft
  recovery, review/run actions, focused styles, and a real browser E2E.
- Migration impact: none. Existing Task JSON columns store the structured overrides; every Attempt
  freezes the resulting effective definition and plan in its existing artifact bundle.
- Compatibility strategy: empty overrides mean complete inheritance. Arrays use only explicit
  `inherit` or `replace`; required workflow outputs cannot be disabled; legacy tasks/runs and
  published versions are not modified. Existing input upload and execution APIs remain authoritative.
- Test plan: inherit/replace semantics, required-output protection, artifact path safety, custom
  output isolation, required input validation, workflow default immutability, draft save/refresh,
  six wizard blockers, final compile, save-ready, save-and-run, frontend build, and real Playwright.

### Result

- Added a pure effective-configuration compiler. It deep-copies the Published Workflow Version,
  applies only explicit node-level `inherit`/`replace` directives, updates both the executable
  definition and DAG plan, synchronizes renamed required artifacts, and never edits the version.
- Added output-level enable/rename controls and Task-only outputs with source-node, artifact path,
  type, and JSON Schema validation. Required outputs cannot be disabled; duplicate and unsafe
  artifact paths fail before a run is prepared.
- Added Task compile validation and changed Attempt preparation to freeze the effective definition,
  effective plan, inputs, and overrides into the run bundle. Empty overrides remain full inheritance.
- Added a six-step `/tasks/new` experience for published workflow selection, Task/workspace metadata,
  dynamic inputs and uploads, execution confirmation/override, output confirmation/customization,
  and final review. Draft IDs and current steps survive refresh through server state and the URL.
- Provider choices come from runtime settings and are limited to artifact-capable executors. Skills
  and MCP Profiles use bounded searchable structured selectors; changing Provider recalculates MCP
  choices. File-set inputs support multiple real uploads instead of storing only the first file.
- Normal users can run the workflow defaults without touching execution configuration. Advanced
  users can switch Agent, Skills, MCP, output names/files, and add schema-backed outputs without JSON.
- Compatibility: migrated terminal-only outputs without an artifact remain runnable, while every
  new Task-only JSON output requires a Schema and every new file output keeps strict path checks.
- Verification:
  - Task store/effective compiler/API compatibility: `8 passed`.
  - Real no-mock six-step browser E2E: `1 passed` after proving required-input blocking, refresh
    recovery, Codex/SFMEA override, output customization, final server compile, and version immutability.
  - Manual browser pass repeated the same flow against the persistent local runtime and saved a
    ready Task with two effective artifacts while the Published Version remained unchanged.
  - Frontend ESLint: passed with zero warnings.
  - Frontend TypeScript and production build: passed.
  - Desktop visual inspection at `1440x900`: bounded selectors, footer, and content fit without
    overlap or horizontal overflow. Screenshot: `output/playwright/phase5/task-wizard-execution.png`.
- Known limitation: the final action enters the Phase 4 compatibility cockpit. Phase 6 replaces it
  with the dedicated run route and live event/quality/delivery hierarchy.
- Next dependency: Phase 6 reads the frozen effective plan and existing public events to present a
  run-focused cockpit without re-deriving Task or Workflow configuration in the browser.

## Phase 6 - Real-Time Run Cockpit

### Before implementation

- Goal: replace the compatibility card stack with one Attempt-focused cockpit where execution,
  quality, delivery, current node, public Agent output, tools, artifacts, and recovery are visible
  from the same frozen run and append-only event stream.
- Expected files: normalized Attempt outcome persistence, retry/diagnostic compatibility APIs,
  a typed live-event hook, `/tasks/[taskId]/runs/[runId]`, cockpit summary/output/tool/event tabs,
  node inspector, deliverable/quality sections, diagnostic drawer, Task links, styles, and E2E.
- Migration impact: no new tables. Newly written run JSON uses the fixed V2 quality and delivery
  enums; deserialization maps Phase 4 compatibility values without rewriting old artifacts.
- Compatibility strategy: all data continues through existing task-run, events, artifacts,
  cancellation, acceptance-audit, and diagnostic-package APIs. `/workbench?task_run_id=` remains a
  valid legacy entry until Phase 8, while Task-owned Attempts link to the dedicated route.
- Test plan: status normalization and outcome persistence; real event pagination/SSE; current-node
  and failure summaries; stdout/stderr/Agent/tool filtering; auto-scroll pause; artifact preview and
  download; diagnostics collapsed; cancel; failed retry lineage; Task links; desktop containment;
  frontend lint/build and no-mock browser coverage.

### Result

- Added the Task-owned `/tasks/[taskId]/runs/[runId]` cockpit and moved Task detail and six-step
  save-and-run navigation away from the compatibility `/workbench?task_run_id=` surface.
- The fixed header separates execution, quality, and delivery state, Attempt lineage, duration, and
  current node. The bounded body separates summary, public live output, tool calls, all events, the
  current-node inspector, deliverables, quality, supporting files, and recovery.
- Connected the existing append-only SSE endpoint directly. The client merges event IDs, caps the
  retained window, keeps Agent/output/error/tool event classes distinct, supports search/node/type
  filters, pause, copy, and user-controlled auto-follow, and never displays private runtime paths.
- Added artifact preview and download from the public artifact manifest. Deliverables are primary;
  inputs/support are collapsed; raw snapshot/events and diagnostic artifacts stay in a closed
  technical drawer with a redacted diagnostic-package download.
- Failure state now answers the failed node, type, Chinese user-readable reason, retryability, and
  reuse behavior in the first viewport. Retry creates Attempt N+1 with parent lineage instead of
  mutating the failed Attempt.
- Parent retry now reconstructs inputs from the parent's copied file snapshot and freezes the parent
  effective definition, compiled plan, execution resources, and output contract. The scheduler seeds
  the child Attempt with successful parent-node results and validated outputs, emits `node_reused`,
  and executes only failed nodes and affected downstream. Later Task edits do not alter the retry.
  The canonical `task_run.json` and `task_bundle.json` are written from the same bundle.
- Normalized legacy outcome values on read and persisted fixed V2 enums. Execution exceptions finish
  quality as blocked, cancellation resets it to not checked, and delivery counts only outputs whose
  runtime status proves generation; an artifact declaration alone no longer means delivered.
- Preserved read-only built-in workflow precedence in both list and detail APIs when V2 is enabled,
  preventing an old same-ID user shadow from replacing an installed preset.
- Verification:
  - Focused retry/outcome/cancel/built-in compatibility tests: passed.
  - Expanded task-run, workflow runner, scheduler, Task, and API regression: `221 passed`.
  - Related real browser E2E (cockpit, Task center, six-step Task wizard): `3 passed` after updating
    the Task-center expectation to the intentional cockpit navigation.
  - Cockpit E2E used mouse hover/click and a real backend Attempt; no request mocking or route
    interception. It verified SSE events, pause/resume, collapsed/open diagnostics, bounded desktop
    layout, `390x844` horizontal containment, and a real child Attempt created by clicking
    “从失败节点重试” with verified parent lineage and retry-source metadata.
  - Frontend full ESLint: passed with zero warnings. TypeScript and production build passed; the new
    dynamic run route is present in the Next.js route manifest.
  - Screenshots: `output/playwright/phase6/run-cockpit-desktop.png` and
    `output/playwright/phase6/run-cockpit-mobile.png` (ignored runtime evidence).
- Compatibility fallback: if a historical parent has no execution snapshot or identifiable failed
  node, retry still creates a frozen child Attempt but executes the full DAG. Current V2 failures use
  node-level retry with successful upstream outputs reused from immutable parent artifacts.
- Next dependency: Phase 7 can link completed run assets into first-class Semantic and Evidence
  libraries while preserving the public/diagnostic audience boundary introduced here.

## Phase 7 - Semantic And Evidence Asset Libraries

### Before implementation

- Goal: replace the legacy import/search form with separate Semantic Case and Evidence asset
  libraries. Users can browse, filter, inspect, edit, deprecate, restore, and trace assets, while
  imports must pass an explicit upload, mapping, preview, conflict, and confirmation flow.
- Expected files: semantic asset management and import-preview services, V2 semantic/evidence API
  routes and tests, complete frontend types/client methods, `/semantic-library` and
  `/evidence-library` pages, a bounded detail drawer, import wizard, navigation, styles, and real
  browser coverage.
- Migration impact: no destructive migration. Existing `semantic_cases`, FTS5, evidence items,
  source slices, and run artifacts remain authoritative. New management metadata is derived from
  existing columns; new-run references may be empty for historical records.
- Compatibility strategy: existing create/import/import-file/search and memory APIs remain unchanged
  for one release. V2 list/detail/update/lifecycle/import-preview/import-commit/facet routes share
  the same stores so old and new clients observe the same assets. This phase continues to label
  search as FTS keyword search, never vector semantic search.
- Test plan: list/facets/filter/detail/update/FTS reindex/deprecate/restore; import preview without
  writes; CSV mapping; explicit text separator; required-field, duplicate-ID, possible-duplicate,
  and unknown-field diagnostics; skip/overwrite/create-new conflict behavior; failed-record export;
  evidence list/detail/source slices; desktop/mobile bounded layout; real mouse/upload/edit/lifecycle
  browser flow; frontend lint/build and expanded backend regression.

### Result

- Added V2 semantic asset APIs for paginated list/filter/facets, detail, safe partial edit, FTS5
  reindex, deprecate, restore, import preview, explicit conflict commit, and NDJSON failure export.
  Existing create/import/import-file/search routes and their permissive compatibility behavior remain
  unchanged.
- Import is now a persisted two-stage operation. JSON, JSONL/NDJSON, CSV, TXT, and Markdown are
  parsed into a non-mutating preview; CSV supports explicit field mapping; text formats require an
  explicit pipe/tab/arrow separator. Missing Case ID, scenario, or expected result never receives a
  generic replacement in the V2 path and cannot be committed.
- Preview reports invalid rows, existing/within-file Case ID conflicts, possible duplicate scenarios,
  and unknown fields. Commit requires exactly `skip`, `overwrite`, or `create_new`; failed records
  retain their original mapped fields and Chinese reasons in a downloadable NDJSON artifact.
- Added `/semantic-library` as a bounded asset table with all specified filters, hit-field/count
  summaries, a contextual detail/editor, copy, deprecate, restore, source, and run-reference views.
  Generated cases with `task_run:<run>:<output>` provenance expose the verifiable Task/Workflow/Run
  relation; unsupported historical relations remain visibly empty.
- Added `/evidence-library` over the existing Evidence Memory store, with keyword/workspace/kind/
  status/source filters, facets, detail, provenance, confidence, and real source slices. No second
  evidence store or synthetic display data was introduced.
- Navigation now exposes separate Semantic and Evidence libraries. Desktop pages keep tables and
  details within the viewport; long fields truncate instead of colliding. Mobile asset routes use a
  compact one-line navigation, full-height detail, and hide the AI dock where it could block import
  controls.
- Verification:
  - Semantic store and V2 asset API tests: `11 passed`, including preview non-mutation, required
    fields, all conflict strategies, FTS reindex, lifecycle, facets, references, and source slices.
  - Expanded semantic/evidence and legacy Workbench API regression: `166 passed`.
  - Real no-mock browser E2E: `2 passed`. The browser uploaded a CSV, mapped fields, previewed an
    invalid row, chose a conflict policy, committed, downloaded failures, edited, deprecated,
    restored, copied, searched Evidence Memory, opened a source slice, and used real mouse hover/
    click and input events.
  - Frontend ESLint passed with zero warnings. TypeScript and production build passed with both new
    routes in the Next.js route manifest.
  - Screenshots: `output/playwright/phase7/semantic-library-desktop.png`,
    `evidence-library-desktop.png`, and `evidence-library-mobile.png` (ignored runtime evidence).
- Known boundary: retrieval remains FTS5 keyword search and is labeled as such. Embeddings, hybrid
  ranking, automatic clustering, and LLM duplicate merging remain outside this release by design.
- Next dependency: Phase 8 can switch V2 on by default, redirect the three legacy Workbench pages,
  remove confirmed dead UI code, complete compatibility/manual documentation, and run the final
  migration, accessibility, performance, and end-to-end release gate.

## Phase 8 - Release Switch, Compatibility, And Acceptance

### Before implementation

- Goal: make Workbench V2 the default product experience, preserve an explicit legacy rollback,
  protect existing SQLite data before migration, and close the release accessibility, performance,
  documentation, and real-browser acceptance gates.
- Expected files: release/config and migration-backup tests, V2 list pagination contracts, legacy
  route switch wrappers, domain-specific semantic/evidence clients and types, bounded event/search
  behavior, release E2E coverage, README/user manual/deployment documentation, and this decision log.
- Migration impact: create a timestamped sibling backup of the existing Workbench SQLite database
  before the first V2 schema migration. The migration remains additive and idempotent; migration or
  backup failure must abort startup without deleting or rewriting legacy rows.
- Compatibility strategy: V2 is enabled when the flag is omitted. Setting
  `WORKBENCH_V2_ENABLED=false` keeps legacy APIs and the legacy Workbench entry usable for one
  release cycle. Existing run directories, event logs, artifacts, semantic rows, and evidence rows
  remain in their original stores and are read in place.
- Test plan: observe red tests for the default/rollback flag, backup-before-migrate behavior, and
  25/100 pagination limits; then run focused and expanded backend suites, frontend lint/type/build,
  all V2 Playwright journeys at 1440/1280/1024/mobile, keyboard and overflow assertions, legacy
  rollback smoke, migration/no-loss checks, secret scan, quality gate, and independent review.

### Result

- Switched `WORKBENCH_V2_ENABLED` to true by default and added a minimal backend-owned release
  endpoint. The three legacy entries now redirect to Tasks, Workflows, and Semantic Library by
  default; a failed release check shows an actionable error instead of guessing.
- Preserved a one-release rollback by dynamically loading the quarantined legacy experience only
  when the backend flag is false. A dedicated false-flag backend and real Chromium proved that
  `/workbench` stays on the legacy route and renders the old cockpit. No database restore is needed.
- Added a one-time, verified SQLite backup before the first V2 migration of existing Workbench data.
  SQLite Backup API includes committed WAL state; `PRAGMA quick_check` must pass, and backup failure
  aborts before schema writes without retaining a partial file.
- Kept old workflow rows, run directories, events, artifacts, semantic cases, evidence, and legacy
  APIs in place. A post-migration integration test reads a pre-V2 run, lists its real Markdown
  artifact, and retrieves the original content through the public compatibility API.
- Added default-25/max-100 server pagination to Tasks, Semantic Cases, and Evidence, 300ms search
  debounce, and bounded previous/next controls. Split active Semantic and Evidence clients/types out
  of the legacy aggregate modules while retaining old exports for rollback callers.
- Fixed long-run event loss: the cockpit now loads the latest 1,000-event tail, pages backward with
  `before_id`, merges incremental SSE by event ID, and retains at most 2,000 loaded events. The API
  reports global latest/first IDs and whether older events exist.
- Fixed a release-discovered cross-runtime bug. An arbitrary frontend on 3013 could probe isolated
  backend 3124 and retry nine CORS preflights because every GET declared JSON content. Only 3123 can
  now infer 3124, and bodyless GET requests no longer send Content-Type. Real Chromium observed one
  GET/200 with no OPTIONS or retry.
- Fixed two keyboard/typing defects found by no-mock workflow E2E: focus now selects a canvas node so
  Delete works, and workflow ID draft normalization preserves a typed trailing separator until final
  submit normalization. The browser completed typed creation, keyboard navigation, node delete/undo,
  mouse drag, server validation/compile, real trial run, and publication in 3.9 seconds.
- Added release accessibility and containment checks. Tasks, Workflows, Semantic, and Evidence pages
  have no page-level horizontal overflow or double main scrolling at 1440, 1280, and 1024 widths;
  primary normal text meets WCAG AA contrast. Phase 6/7 mobile cockpit and asset checks remain green.
- Verification:
  - Backend full suite: `2251 passed, 8 skipped` in `1256.97s`.
  - Phase 8 migration/release plus characterization focus: `8 passed`.
  - Frontend ESLint: passed with zero warnings; `tsc --noEmit`: passed.
  - Next.js production build: passed with all V2 and compatibility routes in the route manifest.
  - Release/static contracts: `4 passed`; legacy workflow canvas contracts: `12 passed`.
  - All V2 real-browser files in the default environment: `9 passed, 1 skipped` in `22.3s`; the
    skipped false-flag case passed separately against the dedicated rollback backend (`1 passed`).
  - Production UI 200-node measurement: 200 nodes materialized in `5077ms`; keyboard focus worked;
    an actual mouse drag completed in `92ms` and moved the node 50px.
  - Secret scan found only intentional synthetic redaction fixtures; no supplied real model key was
    written to tracked source, documentation, logs, screenshots, or artifacts.
- Compatibility boundary: the legacy UI controller is not dead code while rollback remains an
  acceptance requirement, so it was retained and dynamically excluded from the default path. Its
  removal belongs to the next release after a fresh usage audit. Windows native real-machine
  regression remains outside this local macOS gate; Windows command resolution keeps automated
  coverage but is not claimed as an in-person acceptance result.

### Independent review remediation

- The first independent review did not approve Phase 8 and reported five P1, four P2, and one P3
  issue. All ten were reproduced before correction; none was waived.
- Runtime input delivery now follows the frozen plan's `resolved_input_bindings`. Each Agent task
  bundle is rebuilt from only its connected user inputs, and downstream values are resolved from
  validated direct-dependency output ports. Unconnected user text and files no longer leak into
  every Agent node.
- Execution completion is persisted independently from quality outcomes. Legacy weak-success values
  (`needs_review`, `needs_rework`, and `completed_empty`) normalize to completed execution while
  quality remains warning/blocked as appropriate.
- The rollback flag now guards every direct V2 route family (`/tasks`, `/workflows`, Semantic, and
  Evidence), not only the three legacy entry aliases. A dedicated false-flag backend and real browser
  verified each redirect to the corresponding legacy page.
- Creating a draft from a migrated legacy workflow now converts the legacy definition into an
  editable Authoring Graph V2 instead of copying a schema-1 compatibility envelope into the V2
  designer.
- Public run events recursively redact exception strings before append-only persistence. Scheduler
  errors cannot publish provider tokens or similar secrets through SSE or event history. The same
  recursive redaction is applied when legacy JSONL records are read, so pre-upgrade plaintext cannot
  bypass the new append boundary.
- A request-time `stop_on_error=false` can no longer mutate the immutable compiled plan. Task totals
  and status-filter pagination scan beyond 500 rows, while the common unfiltered path uses a SQL
  count plus server pagination. Provider/MCP/Skills overrides are accepted and shown only for Agent
  nodes.
- SSE transient errors keep the browser's native reconnect behavior and refresh state without
  closing the stream permanently. Quiet refreshes merge the server tail into the current bounded
  window. Pausing the cockpit owns a separate visible-event snapshot, so reconnects and high-volume
  live output cannot evict the history the user stopped to read. Task filters use a native select change binding and a complete
  URL replacement because production-browser evidence showed React's delegated select event was not
  committing App Router navigation in this page.
- Fresh post-review verification:
  - Backend full suite: `2259 passed, 8 skipped` in `1249.91s`.
  - Frontend ESLint, `tsc --noEmit`, and production build: passed.
  - Frontend static contracts: `45 passed` across five contract files.
  - Real Chromium V2 journeys with a dedicated false-flag rollback backend: `10 passed` in `22.7s`;
    no mock or route interception was used.
  - The cockpit browser test now proves pause leaves the existing event rows visible. The Task-center
    browser test proves the lifecycle filter changes the URL, then clears an obsolete execution
    filter after a new Attempt changes the task's latest state.
- First re-review remediation verification:
  - Historical read-boundary and scheduler event tests: `11 passed`; three public event API tests:
    `3 passed`.
  - Frontend ESLint, `tsc --noEmit`, production build, and all 45 static contracts passed.
  - The complete real Chromium V2 suite passed `10/10` in `23.9s` on isolated `3123/3124`, with no
    mock or request interception.
  - Final backend full suite: `2260 passed, 8 skipped` in `1232.35s`.
  - The same independent reviewer reported no remaining P0/P1/P2 and returned `APPROVED`. One
    non-blocking P3 records that SSE disconnect is contract-tested but not browser fault-injected.
