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
