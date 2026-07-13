---
feature_ids:
  - workbench-v2
topics:
  - architecture
  - compatibility
  - migration
doc_kind: architecture-decisions
created: 2026-07-13
---

# Workbench V2 Architecture Decisions

## D001 - One Isolated Branch With Phase Checkpoints

- Status: accepted
- Context: the product plan recommends a PR per phase, while the explicit execution contract
  requires one independent worktree and branch, local commits, and no push or PR.
- Decision: use `codex/workbench-v2` with one local checkpoint commit per Phase 0-8.
- Consequence: phase boundaries remain independently reviewable without violating the external
  operation boundary.

## D002 - Feature Flag Defaults To Legacy During Construction

- Status: superseded by D015 in Phase 8
- Context: the V2 data model and routes must be introduced without changing current behavior.
- Decision: `WORKBENCH_V2_ENABLED` defaults to false. V2 code may be deployed dark until Phase 8;
  legacy routes and persisted artifacts remain readable throughout migration.
- Consequence: compatibility tests must cover both flag states and Phase 8 performs the explicit
  default switch.

## D003 - Workflow Versions Share The Legacy Workflows Database

- Status: accepted
- Context: workflow definitions already live in `workbench/workflows.db`, without Alembic.
- Decision: add an explicit schema metadata table and idempotent migrations in the same database.
  Legacy rows are copied to immutable Published V1 records and the old table is retained.
- Consequence: upgrades are atomic and reversible by disabling V2; old task-run snapshots remain
  byte-for-byte unchanged.

## D004 - Legacy Graphs Start Read-Only

- Status: accepted
- Context: legacy `inputs/steps/outputs/ui` cannot always express typed ports and edges safely.
- Decision: migration stores the complete compiled legacy definition and a read-only legacy graph
  envelope. Phase 2's compatibility compiler may produce an executable V2 plan; users edit a copy,
  never the migrated published record.
- Consequence: migration cannot invent dependencies or silently alter execution order.

## D005 - Execution Plans Are Frozen Run Inputs

- Status: accepted
- Context: resolving graph dependencies at runtime would let later edits change historical behavior.
- Decision: compile deterministically at validation/publish time and copy the compiled plan into each
  task-run snapshot. The runner consumes that plan; legacy runs receive an in-memory compatibility
  plan that chains their historical step order.
- Consequence: a run is reproducible and direct dependency outputs can be scoped without consulting
  a mutable workflow record.

## D006 - Scheduler Correctness Before Parallelism

- Status: accepted
- Context: V2 exposes `max_parallelism`, but the first release explicitly prioritizes dependency
  correctness and deterministic artifacts.
- Decision: validate `max_parallelism = 1` in this phase and use a serial DAG scheduler. The scheduler
  still models queued, running, failed, blocked, and independent branches explicitly.
- Consequence: future parallel execution can replace the dispatch loop without changing plans or UI
  status semantics.

## D007 - Server-Owned Graph With Form-Based Authoring

- Status: accepted
- Context: exposing workflow JSON made the designer, cockpit, and execution contract drift apart;
  client-only validation could also publish a plan different from the graph a user saw.
- Decision: the canvas edits Authoring Graph V2 through typed form controls, while draft persistence,
  capability validation, deterministic compilation, workspace resolution, and publication remain
  server-owned. JSON is read-only diagnostic and import/export material, never the normal workflow.
- Consequence: named inputs and outputs drive both authoring and runtime forms, configured providers
  drive Agent choices, and every trial/publish operation executes the same stored graph revision.

## D008 - Tasks In SQLite, Attempts In Existing Artifact Storage

- Status: accepted
- Context: user Tasks need indexed CRUD/filtering, while existing task runs already have immutable
  artifact directories, event logs, restart recovery, hashes, diagnostics, and compatibility APIs.
- Decision: persist Task definitions in `workbench_tasks` within `workflows.db`, and extend each new
  task-run JSON with optional Task/Attempt metadata. Do not copy run payloads into SQLite or rewrite
  historical run artifacts. `last_run_id` is a navigation index, not the Attempt source of truth.
- Consequence: one Task can own many immutable Attempts without duplicating artifacts; legacy runs
  remain readable as history, and execution/quality/delivery state can evolve independently while old
  `status`/`runtime` consumers continue working for one release cycle.

## D009 - Task Overrides Compile As A Pure Projection

- Status: accepted
- Context: per-run Agent, Skills, MCP, and output changes must not mutate the Published Version or
  become an unstructured second workflow definition.
- Decision: store only explicit `inherit`/`replace` directives and Task-only output contracts. The
  server deep-copies and validates the Published Version, compiles an effective definition and plan,
  and freezes both in every Attempt.
- Consequence: empty overrides preserve defaults, retries can reproduce the original configuration,
  and the UI can restore a default without reconstructing the workflow.

## D010 - Migrated Terminal-Only Outputs Stay Runnable

- Status: accepted
- Context: some historical read-only definitions expose terminal output without an artifact filename,
  while V2 output nodes require explicit downloadable artifacts.
- Decision: preserve a migrated output with no artifact when it is not customized. New Task-only and
  customized file outputs still require safe unique artifact paths; custom JSON also requires Schema.
- Consequence: V2 validation remains strict for new work without making historical workflows unusable.

## D011 - Run State Has Three Independent Outcomes

- Status: accepted
- Context: one overloaded status made a failed execution look like a failed delivery, left cancelled
  quality checks pending, and let declared artifact filenames appear as completed deliverables.
- Decision: persist execution, quality, and delivery independently. Compatibility values normalize
  only on read; output delivery requires a positive runtime generation status, not just a contract.
- Consequence: the cockpit can report “execution failed / quality blocked / no delivery” without
  inventing one aggregate state, and historical artifacts remain readable without rewrite.

## D012 - Retry Creates A Frozen Child Attempt

- Status: accepted
- Context: re-running in the same artifact directory destroys history, while compiling the current
  mutable Task can silently change the user's original inputs, Agent resources, or output contract.
- Decision: retry creates Attempt N+1, records `parent_task_run_id`, reconstructs file inputs from the
  parent's copied snapshot, and reuses the parent's effective definition and compiled plan.
- Consequence: the failed Attempt remains immutable and reproducible. The child scheduler reuses
  successful parent results and their validated artifact references, starts at failed nodes, and
  reruns affected downstream nodes. Old runs without an execution snapshot fall back to full-DAG
  execution without pretending that node-level resume occurred.

## D013 - Semantic Import Is Preview Then Commit

- Status: accepted
- Context: legacy file import writes immediately and text import can invent a generic expected
  result, which is acceptable only as a preserved compatibility contract and not as an asset-quality
  workflow.
- Decision: V2 import persists a non-mutating preview, requires explicit CSV mapping or text
  separator selection, validates Case ID/scenario/expected, and accepts only skip/overwrite/create-new
  conflict policies before commit. Failed rows remain downloadable with their validation reasons.
- Consequence: users see exactly what will enter the library and low-quality rows cannot be silently
  normalized into apparently valid test assets. Legacy callers remain operational for one release.

## D014 - Asset Pages Reuse Existing Stores

- Status: accepted
- Context: the old Knowledge page mixed forms while semantic cases, Evidence Memory, FTS indexes,
  source slices, and task-run provenance already have durable stores and consumers.
- Decision: build separate Semantic and Evidence asset APIs/pages directly over those stores. FTS5
  remains the declared search engine. Run references are derived only from verifiable task-run source
  provenance; missing historical relations stay empty.
- Consequence: workflow output materialization, AI retrieval, asset management, and source-slice
  inspection observe the same records without synchronization jobs or a deceptive second database.

## D015 - V2 Is Default With A Server-Owned Rollback Switch

- Status: accepted
- Context: Phase 8 must make V2 the normal experience while retaining a one-release rollback that
  does not rely on rebuilding the frontend or restoring a database.
- Decision: `WORKBENCH_V2_ENABLED` defaults to true. The three legacy route modules ask the backend
  release endpoint and redirect to V2 when enabled; when false they dynamically load the quarantined
  legacy experience. Failure to read release state shows an actionable error instead of guessing.
- Consequence: operations can roll back entry behavior with one backend setting and restart. The
  legacy UI cluster is still referenced and therefore is intentionally retained for this release;
  confirmed deletion is deferred until the compatibility window closes.

## D016 - Migration Backup Is Verified And One-Time

- Status: accepted
- Context: additive SQLite migration still needs a recovery point, including committed WAL data,
  before any V2 schema write occurs.
- Decision: before the first migration of a database containing Workbench data, use SQLite's backup
  API to create a timestamped sibling file and require `PRAGMA quick_check = ok`. Never overwrite an
  existing verified pre-V2 backup. Backup failure aborts migration and removes a partial backup.
- Consequence: migration startup is fail-closed and has an auditable pre-write recovery point. Normal
  feature-flag rollback does not restore this file because both schema generations remain readable.

## D017 - Live Events Start At The Tail And Page Backward

- Status: accepted
- Context: loading the first 1,000 events and then opening SSE at the global latest ID silently
  skipped the middle of long runs; repeatedly downloading the full log also violates the release
  performance contract.
- Decision: the event API supports an explicit latest-page tail and `before_id` pagination. The
  cockpit starts with the newest 1,000, allows older pages on demand, merges SSE increments by ID,
  and retains at most 2,000 events in memory.
- Consequence: no event gap is hidden by the initial page limit, current progress appears quickly,
  and long histories remain available without unbounded DOM or network growth.

## D018 - New Frontend Domains Split Without Removing Compatibility Exports

- Status: accepted
- Context: V2 pages must stop expanding the giant API/type modules, while legacy pages and one-cycle
  API compatibility still import those modules.
- Decision: active V2 Workflow, Task, Run, Semantic and Evidence code uses domain clients and types.
  Keep old exports and the legacy controller only as a rollback boundary until the next release.
- Consequence: new code has explicit ownership and bounded imports without creating a big-bang
  migration that would break rollback. Removing the compatibility cluster requires a later usage
  audit and its own release decision.

## D019 - Test Runtime Ports Are Never Universal API Fallbacks

- Status: accepted
- Context: the shared API client offered both 3004 and isolated-test 3124 to every frontend origin.
  A frontend on 3013 therefore retried a read request nine times against another worktree's backend,
  producing CORS failures, noise, latency, and possible cross-runtime data access.
- Decision: only a frontend actually running on 3123 may infer backend 3124. Other deployments use
  their configured API base and the public 3004 fallback. Bodyless GET requests do not send a JSON
  Content-Type and therefore do not create an unnecessary CORS preflight.
- Consequence: isolated E2E cannot be discovered by unrelated frontends, AI Mini Dock polling is one
  GET instead of OPTIONS plus GET, and custom ports must be configured explicitly as documented.

## D020 - Frozen Port Bindings Define Agent Input Scope

- Status: accepted
- Context: preparing one global context bundle for every Agent makes the canvas misleading and can
  expose an unconnected input to a node that was never intended to receive it.
- Decision: compile named data edges into input bindings, rebuild each Agent handoff from only bound
  task inputs, and resolve runtime target ports from the input snapshot or validated outputs of direct
  dependencies. Request-time execution options cannot rewrite this frozen plan.
- Consequence: the words, files, links, MCP requests, retrieval query, test-activity contract, and
  downstream values received by an Agent match the graph. Legacy definitions without binding metadata
  retain their historical all-input behavior only on the compatibility path.

## D021 - Public Run Events Are A Redacted Boundary

- Status: accepted
- Context: scheduler exceptions can contain provider messages, command lines, and credential-shaped
  values before the API has a chance to translate an error for users.
- Decision: recursively redact every string in a public event payload both before append-only
  persistence and whenever an event is read from current or legacy JSONL. Raw process diagnostics
  remain confined to diagnostic artifacts and their existing access path.
- Consequence: SSE, event history, clipboard, and public JSON cannot expose an exception secret merely
  because a node failed. User-facing status remains useful while technical detail stays downloadable
  through the diagnostic boundary.

## D022 - Phase 8 Deployment Is Single Process

- Status: accepted
- Context: migration backup serialization and Attempt-number allocation currently use process-local
  locks around SQLite/filesystem operations. Multiple Uvicorn workers could bypass those locks.
- Decision: the supported Phase 8 deployment runs exactly one backend application process, as shown
  in `docs/DEPLOYMENT.md`. Do not add `--workers` or start parallel backend instances against the same
  data directory. Multi-process support requires a database-owned lease/transaction design first.
- Consequence: current backup and Attempt guarantees are valid for the documented deployment. This is
  an explicit operational boundary rather than an unclaimed multi-worker safety property.

## D023 - Paused Event History Is A Snapshot, Not A Moving Filter

- Status: accepted
- Context: retaining only an event-ID boundary inside the moving 2,000-event live window allows
  reconnect refreshes or sustained output to evict every row the user paused to inspect.
- Decision: quiet refreshes merge the latest server tail into the active bounded window, while the
  pause action captures a separate immutable visible-event snapshot. Resume discards that snapshot
  and returns to the live window.
- Consequence: background execution and native SSE reconnect continue without unbounded memory, but
  the user's paused reading view remains stable until they explicitly resume.
