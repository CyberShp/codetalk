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

## D002 - Feature Flag Defaults To Legacy

- Status: accepted
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
