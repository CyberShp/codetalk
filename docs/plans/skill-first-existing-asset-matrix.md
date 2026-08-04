---
feature_ids: [F014]
topics: [asset-matrix, migration, workflow-removal, runtime-reuse]
doc_kind: plan
created: 2026-08-04
---

# F014 Existing Asset Matrix

## Scope

This matrix is a main-only caller/callee audit for F014 Task 0. Evidence was
read at main baseline `9e1434d9`; no F012/F013 code or history is an input. A
classification is an implementation boundary, not permission to preserve a
Workflow product surface after the Phase E removal gate.

| Area | Main assets | Classification | Caller/callee evidence | F014 treatment |
|---|---|---|---|---|
| Workflow product/version | `workflow_version_store.py`, `workflow_dsl.py`, `workflow_presets.py`, `workbench_v2_workflows.py` | Remove | `main.py` registers `workbench_v2_workflows.router`; `workbench_v2_tasks.py` calls `WorkflowVersionStore` and preset availability checks. | Replace only after Skill Task creation and vertical run are proven. Do not migrate authoring canvas state. |
| Workflow canvas/UI | `frontend/src/app/workbench/workbench-controller.ts`, workflow builder utilities, `workflow-view.tsx`, `run-view.tsx`, and `agent-workbench-experience.tsx` | Remove | The controller owns workflow builder/preset state and actions; `workflow-view.tsx` and `run-view.tsx` render the canvas and cockpit, composed by `agent-workbench-experience.tsx`. | Replace the user journey with Skill Project/Version/Task/Cockpit surfaces. Retain generic artifact/event display concepts only after extraction. |
| Task | `workbench_task_store.py`, `workbench_v2_tasks.py` | Adapt | `main.py` registers Task router; router validates a published `WorkflowVersionStore` version then calls `WorkbenchTaskStore.create_task`; store persists `workflow_id` and `workflow_version_id`. | Replace binding with exactly one released Skill Version and Skill digest, inputs, runtime/model/budget, and selected deliveries. No dual Workflow/Skill write path. |
| Run Attempt | `workbench_task_run.py` (`WorkbenchTaskRunPreparer`, `WorkbenchTaskRunStore`) | Adapt | `workbench_v2_tasks.py` and `agent_workbench.py` call `WorkbenchTaskRunPreparer.prepare`; `WorkbenchWorkflowRunner.execute_task_run` loads the stored Attempt. | Keep Attempt directories and frozen-snapshot discipline; add the Skill Run Invocation as the sole frozen bridge. |
| Task configuration compiler | `workbench_task_compile.py` | Adapt | `workbench_v2_tasks.py` and `WorkbenchTaskRunPreparer` call `compile_task_configuration`; it currently applies Workflow inputs, outputs, runtime overrides, and V3 restrictions. | Replace Workflow overrides with Skill IR input, runtime/budget, and delivery selection validation. Do not carry dynamic custom-output or staged-analysis policy into Skill Tasks. |
| Task Bundle and Run Snapshot | `workbench_task_run.py` (`task_bundle.json`, `build_run_snapshot_v3`, `validate_run_snapshot_v3`, frozen component loaders) | Adapt | `WorkbenchTaskRunPreparer.prepare` writes `task_bundle.json` and `run_snapshot_v3.json`; the runner validates/loads them, and startup recovery calls `validate_run_snapshot_v3`. | Preserve frozen component digests and fail-closed loading, but make Skill Invocation, IR, input snapshot, capability/preflight receipts, and Session declarations the frozen components. |
| Workflow execution runner | `workbench_workflow_runner.py` (`WorkbenchWorkflowRunner`) | Adapt | `agent_workbench.py`, `knowledge_center.py`, and Task APIs call `execute_task_run`; runner loads frozen V3 authority, dispatches nodes, writes checkpoints. | Main integrator adapts the hot file behind a Skill executor. Remove Workflow terminology/contracts only after callers move. |
| Scheduler | `workflow_scheduler.py` (`WorkflowDagScheduler`) | Adapt | `WorkbenchWorkflowRunner` invokes it for V3 and compiled-plan execution; it verifies topological order, serial execution, dependency blocking, reuse seeds, waiting, and failure policy. | Reuse deterministic ordering and stop/wait semantics behind the Skill executor, driven only by validated Skill IR. Remove Workflow naming after the vertical gate; do not create a second scheduler. |
| Execution ownership lease | `workflow_execution_lease.py` (`WorkflowExecutionLeaseStore`) | Reuse | `agent_workbench.py` acquires, heartbeats, and releases the Attempt-local lease around background execution. | Keep the durable single-owner lease and corruption checks for Skill Attempts. Rename persisted filenames only in a versioned migration; do not add a parallel process lease. |
| Workflow graph and V3 compiler | `workflow_graph.py`, `workflow_contract_v3.py` | Remove | `workbench_v2_workflows.py` validates/compiles authoring graphs; `WorkflowVersionStore` compiles legacy definitions; `workflow_graph.py` delegates V3 validation/compilation to `workflow_contract_v3.py`. | The new Skill validator/IR compiler replaces Workflow graph authoring and compilation. Keep them only until published Skill Versions and Task creation pass the vertical gate. |
| Workflow node authoring registry | `workflow_node_registry.py` | Remove | `workflow_graph.py`, workflow APIs, and authoring factory consume node definitions; `agent_workbench.py` exposes registry payload to the current designer. | Skill schemas and source files define steps; do not preserve a dynamic node palette or node registry as a second methodology source. |
| Handler capability registry and dispatch | `workflow_handler_registry.py`, `workflow_handler_dispatcher.py` | Adapt | Workflow APIs/authoring resolve handler availability from the registry; `WorkbenchWorkflowRunner` dispatches explicit validator/governance nodes through `WorkflowHandlerDispatcher`. | Retain domain-neutral validators or governance handlers only when explicitly declared by Skill IR. Remove authoring-palette coupling and hard-coded professional handlers that are not part of the released Skill contract. |
| Harness/provider boundary | `harness_facade.py`, `agent_run_harness.py`, `provider_adapters/`, `agent_runtimes.py` | Adapt | `WorkbenchTaskRunPreparer` and `agent_workbench.py` prepare Harness runs; `AgentHarnessFacade.prepare/execute` delegates to ProviderAdapter, reports adapter-declared capabilities, emits lifecycle events, and performs controlled artifact collection. The harness writes a capability manifest; active provider discovery is not yet a Facade capability. | Keep adapter isolation, sandboxing, capability reporting and artifact collection. Add F014 preflight discovery, Session/Process persistence, and the common capability report at the adapter seam. |
| CLI process bridge | `agent_cli_bridge.py`, `provider_adapters/cli_base.py` | Adapt | `CliProviderAdapter` calls `stream_agent_runtime`; the bridge spawns the subprocess, isolates its process group, watches cancellation, applies idle/overall timeout, and performs bounded terminate/kill cleanup. It does not expose a durable Process identity or generation today. | Keep the proven spawn, sandbox, process-group kill, and timeout mechanics. Add exact Process identity/generation receipts and exit callbacks so `terminate_session` targets one Process and emits nonterminal lifecycle evidence. Do not create a second CLI launcher. |
| Durable child sessions | `child_session.py` (`ChildSessionStore`) | Adapt | `WorkbenchWorkflowRunner` creates/claims child sessions for subagent nodes; the store already persists deterministic claims, status, events, output, and declared artifacts under an Attempt. Its schema is child-node specific, not the F014 Producer/Judge Session contract. | Reuse its lock/atomic-write/idempotent-claim patterns or adapt the store deliberately for F014 Session records. Do not equate current child sessions with provider Sessions, and do not create a parallel Session store without evaluating this asset. |
| Tool action replay | `tool_action_journal.py` (`ToolActionJournal`) | Adapt | `WorkbenchWorkflowRunner` creates the Attempt-local journal and injects it into `AgentHarnessFacade`; Harness `begin/complete/fail` prevents provider tool calls from being replayed ambiguously. | Preserve durable tool-call idempotency and conflict behavior; attach Invocation/Session/Process provenance and map tool events to the F014 public vocabulary. |
| Checkpoints | `node_checkpoint.py`, `checkpoint_projection.py` | Reuse | `WorkbenchWorkflowRunner` calls `NodeCheckpointStore.commit_completed`; `checkpoint_projection.rebuild_checkpoint_projection` reads committed files and emits projection events. | Keep atomic idempotent checkpoint persistence. Extend checkpoint provenance with F014 Session/Invocation references; do not treat process memory as durable state. |
| Run events | `workbench_task_run_events.py` | Reuse | Runner and recovery use `WorkbenchTaskRunEventStore`; `append_once` stores durable deduplication keys; `main.py` calls interrupted-run reconciliation at startup. | Map F014 public lifecycle events into this append-only store. Preserve ordering and use deduplication for replay/cancel receipts. |
| Execution/quality/delivery state | `workflow_run_status.py` | Reuse | `WorkbenchTaskRunEventStore`, runner, startup recovery, and Agent API all use its status validation and delivery derivation. | Preserve distinct execution, artifact validation, governance/Judge, and delivery projections. Map Skill-required Judge state without collapsing Producer completion into `READY`. |
| Startup recovery | `workflow_startup_recovery.py`, `workbench_task_run_events.py` | Adapt | `main.lifespan` invokes `reconcile_v3_startup_recovery`, schedules recoverable runs through `agent_workbench`, then reconciles legacy interrupted runs. | Reconcile frozen Skill Invocation plus valid checkpoints, Session invalidation, and process cleanup. Do not read Drafts. |
| Retry and cancellation | retry policies in `workbench_task_compile.py`/runner, cancellation API and event receipts in `agent_workbench.py`/`workbench_task_run_events.py`, Harness cancel callback | Adapt | The runner applies frozen retry policy and checks cancellation between durable transitions; Harness execution accepts `is_cancelled`. Current `cancel_task_run` has no `operation_id` receipt, ignores the transition boolean returned by `mark_status_unless`, and appends rather than deduplicates the terminal event. | Preserve bounded retry and cancellation checks, but add Attempt-scoped operation receipts, deduplicated terminal projection, exact Process targeting, and process-tree cleanup without adding a second cancellation authority. |
| Artifact authority | `workbench_artifact_manifest.py`, `artifact_contract_v3.py`, `validators/` | Reuse | Runner writes manifest through `write_task_artifact_manifest`; manifest rejects symlink escape and hashes files; contracts classify artifact layers. | Reuse path/hash/symlink enforcement. Bind the Skill IR's required artifacts to this authority and distinguish staged from committed output. |
| Artifact validation pipeline | `validators/`, reusable validation paths in `workflow_handler_dispatcher.py` | Adapt | `WorkflowHandlerDispatcher` invokes `DEFAULT_VALIDATOR_REGISTRY` for declared outputs; runner and manifest code consume validated output records. | Bind validators to Skill IR artifact declarations and Judge policy. Remove Workflow node/palette assumptions while retaining deterministic local validation. |
| Deliveries | `workbench_deliverables.py`, `artifact_profiles.py`, deliverables API | Adapt | `build_task_run_deliverables` derives accepted paths then `build_deliverable_bundle` creates a deterministic ZIP; `main.py` registers deliverables router. | Treat selected Skill deliveries as package/presentation filtering only. Execution still produces all required internal artifacts. |
| Cockpit | `frontend/src/app/workbench/workbench-controller.ts`, `run-view.tsx`, and Task/run APIs | Adapt | The controller polls Task Runs and consumes event streams; `run-view.tsx` renders the cockpit; APIs expose run summary, artifacts, events, and cancellation. | Reuse status/event/artifact mechanics after extracting them from Workflow-labelled UI; add current Skill step, next action, capability degradation, Judge and delivery state. |
| Hard-coded Workbench skills | `workbench_skills.py`, calls from `workbench_task_run.py` and runner paths | Remove | `workbench_task_run.py` imports `resolve_workbench_skill_instructions`; legacy execution paths use Workbench-specific instruction resolution. | Skill Version source/IR supplies methodology. The F014 professional path must not infer instructions from target prose. |
| Staged professional analysis | `legacy_workflow_execution.py` and `ai_staged_execution` call paths in runner/API | Remove | `workbench_task_run.py` imports `legacy_workflow_execution`; runner contains staged lifecycle helpers; legacy API paths execute the old professional route. | Gate with source search and regression before deletion. Preserve generic Harness/Artifact machinery, not staged professional policy. |

## Caller-Callee Boundaries

The intended post-F014 control flow is:

```text
Skill Version + Task configuration
  -> Skill Run Invocation (frozen)
  -> Attempt store / event store / checkpoint store
  -> Skill executor adapter
  -> Harness facade / provider adapter / Agent Process
  -> artifact contract + manifest
  -> Judge adapter (separate Session when required)
  -> delivery bundle + Skill Cockpit
```

The existing callable seams to preserve during implementation are:

- `WorkbenchTaskRunPreparer.prepare` writes the durable Attempt preparation;
  F014 moves its input from compiled Workflow data to frozen Skill Invocation.
- `WorkbenchWorkflowRunner.execute_task_run` is the current attempt executor;
  the main integrator owns its adaptation so concurrent sub-Agents do not
  conflict in this hot file.
- `AgentHarnessFacade.prepare` and `.execute` expose a provider/session
  boundary; F014 must add persistence around them rather than duplicate a CLI
  launch path.
- `agent_cli_bridge.stream_agent_runtime` and its bounded process-group cleanup
  are the concrete Process seam; F014 adapts them to expose durable identity and
  nonterminal exit evidence.
- `ChildSessionStore` and `ToolActionJournal` are existing Attempt-local durable
  primitives whose claim/replay behavior must be adapted before introducing any
  F014 Session or tool-action persistence.
- `NodeCheckpointStore.commit_completed` is the checkpoint-before-projection
  enforcement point.
- `WorkbenchTaskRunEventStore.append_once` is the recovery/cancellation
  idempotency mechanism.
- `reconcile_v3_startup_recovery` is the closest existing frozen-run recovery
  path; it needs Skill Invocation validation rather than a parallel service
  restart subsystem.

## Explicit Exclusions

- Do not copy F012 benchmark, evaluator, corpus, or baseline-freezer assets.
- Do not copy F013 lifecycle types, state names, or implementation history.
- Do not retain Workflow Version or canvas objects as a compatibility source of
  truth for Skill Tasks.
- Do not add a second artifact authority, object storage abstraction, or a new
  process manager before a demonstrated need beyond the main Harness boundary.

## Phase Ownership Guard

Until the vertical gate passes, only the main integrator modifies
`workbench_task_store.py`, `workbench_task_run.py`,
`workbench_workflow_runner.py`, `main.py`, and shared frontend API/types. Slice
owners can add new Skill modules and focused tests, but their handoff must name
the exact caller/callee affected and retain this matrix's classification.
