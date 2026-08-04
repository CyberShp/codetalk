---
feature_ids: [F014]
topics: [adr, skill-runtime, agent-lifecycle, invocation, harness, limits]
doc_kind: architecture-decision-record
created: 2026-08-04
status: accepted
---

# ADR-029: Skill Invocation Uses the Main Durable Runtime Stack

## Context

F014 needs a Skill-specific execution contract without creating a competing
runner.  `main` already owns Task Run orchestration, Harness, checkpoint, event,
artifact, cancellation, recovery, and cockpit mechanisms.  A provider CLI
process is disposable; it cannot become the authoritative owner of Run state,
artifacts, or recovery.  This ADR deliberately uses only `main` capabilities
and F014 requirements, not F012/F013 code or lifecycle vocabulary.

## Decision

The versioned **Skill Invocation** is the sole frozen bridge from Skill domain
to the existing main runtime stack through its Harness seam.

- Before execution, a Run Attempt freezes the Skill Version ZIP, IR, content
  digest, input snapshot, selected deliveries, artifact root, Agent runtime,
  capability report, preflight receipt, model/budget declaration, and Judge
  declaration in its Invocation.
- The main **Task Run orchestration and store stack** is the sole durable owner
  of task identity, queueing, events, checkpoints, artifact authority,
  cancellation, recovery, terminal status, and cockpit projection.  The
  Harness is only the provider/process seam: it launches, observes, and stops
  provider work and returns normalized events and artifact references.  It
  cannot create an alternative persisted Run state or artifact store.
- **Run Attempt**, **Agent Session**, and **Agent Process** are separate
  persisted concepts.  A Session record holds resumable context identity,
  capability and checkpoint linkage; any in-process Session map is a
  disposable cache, never durable authority.  A Process is one disposable
  execution of that Session.  Process death preserves committed
  checkpoints/artifacts and discards uncommitted temporary output.
- Lifecycle receipts are ordered from capability discovery and preflight through
  Session creation, process start, message/tool/artifact activity,
  waiting/resume, and exactly one terminal state.  Restart reconciliation uses
  the frozen Version and Invocation, never a mutable Draft.
- Capability reports declare support or explicit non-support for resume, tools,
  cancellation, session isolation, declared context capacity, and requested
  output limit.  Unsupported behavior is degraded or blocked by contract; it
  is never silently ignored.
- Mandatory F014 real-provider invocations declare a 200,000-token context
  capacity and request at most 4,096 output tokens.  `200000` describes model
  capacity, not the prompt size to send.  Preflight receipts record requested
  runtime/provider/model, observed CLI/runtime version, endpoint class or
  approved-host hash, credential-ready boolean, status, timestamp, and
  effective model when observable.  Credentials never enter Invocations,
  events, logs, artifacts, screenshots, or handoffs.

For the final profile, CodeTalk and the bounded Clowder AI comparison use
OpenCode with `deepseek/deepseek-v4-flash`; CodeTalk product LLM and AI Review
use OpenAI-compatible `deepseek-v4-flash`.  The three surfaces have separate
preflight receipts and do not assume shared credentials.

## Consequences

- F014 adds a thin Invocation/executor adapter and common lifecycle contract;
  it does not rewrite the main Harness or introduce a new distributed runtime.
- Fake Agent tests control lifecycle transitions deterministically.  Real
  runtime tests run the same contract where supported and preserve explicit
  degradation evidence for CodeAgent, Claude Code, and OpenCode.
- Recovery after process kill, CodeTalk restart, or one invalid Session resumes
  from the last committed checkpoint when supported.  Missing/corrupt Session
  recovery is recorded and may create only one clean Session per Attempt before
  explicit failure; cancellation is idempotent and cleans child processes.
- Queue, Agent, script, validation, and overall timeouts remain separate
  terminal reasons and evidence fields.

## Non-Goals

- Rewriting the main Task Run orchestration/store stack or introducing a second
  durable runtime, event log, checkpoint store, or artifact store.
- Treating a provider process or an in-memory Session map as durable recovery
  authority.

## Alternatives Considered

- Make Harness the durable runtime owner: rejected because provider/process
  seams are disposable and cannot authoritatively recover Task Runs.
- Persist only provider process state: rejected because process death must not
  erase Session linkage, committed checkpoints, or artifacts.

## Affected Scope

Skill Invocation, Task Run orchestration/store, lifecycle records, Harness
adapters, provider capability/preflight receipts, recovery, cancellation, and
cockpit projection use this boundary.  Provider CLIs remain behind the Harness
seam.

## Rollback

Disable the F014 Invocation adapter and restore the prior `main` execution
path from the migration backup.  Retain frozen Invocations, Run Attempts,
events, checkpoints, and artifacts as read-only evidence; do not reconstruct
them from provider process state or in-memory maps.

## Validation

Acceptance requires a Fake Agent lifecycle matrix for create/start, events,
process kill, service restart, Session loss, duplicate cancellation, layered
timeouts, Judge isolation, and capability degradation.  The final gate repeats
the applicable contract using the mandatory real OpenCode/DeepSeek profile,
records non-secret receipts, and does not treat a health probe or mock as a
real-provider pass.
