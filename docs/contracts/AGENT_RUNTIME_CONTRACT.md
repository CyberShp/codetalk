---
feature_ids: [F014]
topics: [agent-runtime, lifecycle, invocation, recovery, provenance]
doc_kind: contract
created: 2026-08-04
---

# F014 Agent Runtime Contract

## Purpose and Authority

This contract defines the Skill-to-Agent boundary for F014. It is based only on
capabilities and persistence assets present on `main` at `9e1434d9`:
`WorkbenchTaskRunStore`, `WorkbenchTaskRunEventStore`, `NodeCheckpointStore`,
`WorkbenchWorkflowRunner`, `AgentHarnessFacade`, the artifact manifest, and
startup recovery. It does not adopt F012/F013 source, event vocabulary, or
compatibility assumptions.

The durable authority is the Task Run/Runner/Store stack: it owns frozen
invocations and snapshots, state transitions, append-only events, checkpoints,
and committed artifact manifests. `AgentHarnessFacade` is the provider/process
seam below that authority. It can prepare or operate a provider session, but
does not own Task state, checkpoint authority, delivery selection, or terminal
state. The operation surface below is a common F014 contract projected through
those existing components; it does not create a separate session service,
event bus, or artifact subsystem.

The F014 Skill Run Invocation is the immutable authority passed to execution.
An Agent response, local process memory, mutable Skill Draft, or UI projection
cannot amend it. The invocation freezes the released Skill ZIP, IR, content
digest, input snapshot, selected deliveries, Agent runtime request, Judge
declaration, artifact root, and provenance before process start.

## Durable Identities

| Concept | Identity | Persistence | Lifecycle rule |
|---|---|---|---|
| Run Attempt | `task_run_id` | Attempt directory, frozen invocation/snapshot, event log, checkpoints, artifact manifest | The durable task truth. It has exactly one terminal state. |
| Agent Session | `agent_session_id` plus runtime ID | Invocation and Session record under the Attempt | A resumable runtime context. It may outlive a process and is never the Producer/Judge shared context. |
| Agent Process | `agent_process_id` plus OS/process metadata | Process record and lifecycle events | A disposable execution instance. Killing it must not remove a committed checkpoint or a valid Session. |

One Attempt may have multiple sequential Processes. It normally has one active
Producer Session. A required Judge creates a separate Judge Session and may
never receive the Producer transcript. Clean Session replacement after
invalidation is allowed at most once across the entire Attempt. The Attempt
persists `clean_session_recovery_count` and the replacement receipt; any later
Session invalidation fails with `session_recovery_exhausted` instead of creating
another replacement.

## Frozen Invocation

Before `session_started`, F014 persists a `skill-run-invocation-v1` document
with the following required groups. Credentials and raw environment values are
never persisted.

| Group | Required fields | Rule |
|---|---|---|
| Identity | attempt, Task, Skill Version IDs; Skill content digest; invocation digest | IDs and digests are immutable for the Attempt. |
| Inputs | input snapshot reference/digest; `declared_context_refs` | Draft files and live UI values are not execution inputs. |
| Runtime request | runtime ID, provider, `requested_model`, budget, requested capabilities | Requested is user/Task intent, not a claim that the runtime accepted it. |
| Runtime effective | `effective_model`, effective provider, capability report, CLI/runtime version | Persist after preflight; any mismatch is visible and policy-checked. |
| Limits | `declared_context_window_tokens`, `requested_max_output_tokens`, timeout budget | For fixed F014 acceptance: 200000 declared context capacity and at most 4096 requested output tokens. |
| Output | selected delivery IDs, complete required-artifact contract, artifact root | Delivery filtering cannot omit upstream Skill steps or internal artifact production. |
| Judge | required/optional policy, isolated runtime/session request, checked artifact scope | Judge has frozen inputs, Skill contract, and artifacts only. |
| Provenance | source/release/IR digests, preflight receipt, endpoint host or approved-host hash, credential-ready boolean | No credential, secret, raw prompt transcript, or unredacted environment value. |

`declared_context_refs` means the bounded list of input/material/artifact references
made available to the Agent, each with a content digest and access scope. It is
not a copy of an entire workspace nor a claim about model context capacity.
`requested_max_output_tokens` is only the explicit output-token ceiling;
structured artifact and delivery obligations remain in the Output group. The
recorded `effective_model` must be the runtime-reported accepted model ID or
`unknown`; it must never silently repeat the requested value when discovery
cannot establish it.

## F014 Operation Surface

Each operation is invoked by the Task Run/Runner against frozen invocation
data. `task_run_id`, `agent_session_id`, and `operation_id` are required where
shown. `operation_id` is a caller-supplied durable idempotency key; it is stored
through the existing run event/checkpoint records, never only in process memory.
The event stream is the existing append-only Task Run event log, after the
Runner maps private Harness/provider callbacks to the public event vocabulary.

| Operation | Request | Result / public events | Idempotency and error semantics | `main` mapping |
|---|---|---|---|---|
| `discover_capabilities` | Frozen runtime request, provider/runtime identity, requested model and limits. No credentials in payload. | Capability report; `capability_discovered`, then `preflight_passed` only when policy passes. | Same invocation/runtime fingerprint returns the persisted report. Unobservable capability is `unknown`, not `supported`; unavailable runtime or policy failure prevents session creation. | Existing `AgentHarnessFacade.capabilities()` plus Runner preflight; F014 persists the common report. |
| `create_session` | `task_run_id`, role (`producer` or `judge`), frozen runtime request, `operation_id`. | Session identity and `session_created`. | Same key returns the recorded Session. A terminal Attempt, failed preflight, or forbidden Producer/Judge sharing returns a typed refusal; no provider process starts. | Existing Harness `prepare()`; F014 assigns durable Session record and role isolation. |
| `start_session` | `task_run_id`, `agent_session_id`, frozen input references, timeout budget, `operation_id`. | Process identity/generation/status and `session_started`; then mapped runtime events. | Same key returns the recorded start receipt and must not launch a second process. Invalid Session, cancellation intent, or unsupported required capability fails before launch. | Existing Harness `execute()`; F014 records Process identity and public lifecycle projection. |
| `send_input` | `task_run_id`, `agent_session_id`, immutable input reference or explicit approved continuation, `operation_id`. | Accepted input receipt; a continuation that clears a wait emits `resumed`, while later Agent output emits `agent_message`. | Repeated key is a no-op returning the original receipt. Input not frozen/approved, terminal Session, or provider without interactive input is refused; it never mutates a release or Draft. | F014 addition over the current one-shot `prepare()/execute()` seam. |
| `stream_events` | `task_run_id`, optional `after_event_id`, bounded page/stream limit. | Ordered redacted public events, including `session_created`, `session_started`, `agent_message`, `tool_started`, `tool_finished`, `artifact_written`, `checkpoint_created`, `waiting_for_input`, `process_exited`, `process_terminated`, `resumed`, `cancelled`, `failed`, and `completed`. | Read-only and repeatable. A missing Attempt returns not-found; an invalid cursor is rejected. Private provider event kinds remain diagnostic-only. | Existing `WorkbenchTaskRunEventStore.list_after()` and Harness `event_sink`; F014 standardizes names/projection. |
| `checkpoint_session` | `task_run_id`, `agent_session_id`, node/step identity, frozen-input hash, artifact hashes, provider resume provenance, `operation_id`. | Durable checkpoint receipt and `checkpoint_created`. | Same checkpoint key with identical content returns the committed checkpoint; conflicting content raises the existing checkpoint conflict and does not overwrite. | Existing `NodeCheckpointStore.commit_completed()` and checkpoint projection; F014 ties it to a Session. |
| `resume_session` | `task_run_id`, `agent_session_id`, committed checkpoint/resume token, timeout budget, `operation_id`. | Process receipt, `resumed`, `session_started`, and subsequent mapped events. | Same key returns the prior receipt. Missing/corrupt checkpoint or `resume=unsupported` is explicit; the one Attempt-wide permitted clean Session replacement is recorded before retry. | Existing Harness `resume()` plus Runner recovery/checkpoint projection; F014 adds the lifecycle receipt. |
| `cancel_session` | `task_run_id`, `agent_session_id`, reason, `operation_id`. | Cancellation receipt; eventual `cancelled` terminal event when active work stops. | Repeats return the original receipt. It is valid while nonterminal; a terminal Attempt returns its existing terminal receipt. Unsupported provider cancellation still records intent and uses the Runner/process cancellation path or fails explicitly by policy. | Existing Harness `cancel()` and Runner cancellation callback; F014 persists intent and terminal projection. |
| `terminate_session` | `task_run_id`, `agent_session_id`, exact process identity/generation, reason, `operation_id`. | Process termination receipt and nonterminal `process_terminated`; the owning Attempt may later emit `resumed` or one terminal event. | Repeats do not kill a replacement Process. A missing/already-exited Process returns a stable no-op receipt; it never deletes a Session, checkpoint, or committed artifact. | F014 adaptation over existing Runner and CLI-bridge process-tree termination; durable Process identity is new. |
| `get_session_status` | `task_run_id`, `agent_session_id`. | Durable Session, latest Process, capability/degradation, checkpoint, and terminal/awaiting-input status. | Read-only and repeatable. Missing identities return not-found; it does not infer state from live process memory alone. | Existing Task Run Store, event log, checkpoint store, and Harness result; F014 composes them. |
| `get_session_artifacts` | `task_run_id`, `agent_session_id`, optional declared-artifact filter. | Validated committed artifact manifest entries and digests; `artifact_written` is emitted on commit, not merely discovery. | Read-only and repeatable. It excludes uncommitted staging and undeclared files; missing Attempt/Session returns not-found. | Existing Harness `collect_artifacts()` and artifact manifest validation; F014 scopes the query to Session provenance. |

`create_session`, `start_session`, `send_input`, `checkpoint_session`,
`resume_session`, `cancel_session`, and `terminate_session` are command
operations. `discover_capabilities`, `stream_events`, `get_session_status`,
and `get_session_artifacts` are query operations. A command may return its
durable receipt before an asynchronous process reaches its next lifecycle
event; only the Task Run terminal transition determines Attempt completion.

## Preflight and Capability Report

The executor must append a durable `capability_discovered` event and persist a
capability report before creating an Agent Session. Preflight checks:

1. the frozen Skill Version, IR, and input snapshot digests resolve locally;
2. the artifact root is writable, bounded, and not a symlink escape;
3. runtime/provider/model request, limits, CLI version, and credential-ready
   status are observable without exposing credentials;
4. required capabilities (`resume`, tool call, artifact collection,
   cancellation, isolated Judge) are either supported or explicitly refused;
5. requested/effective model mismatch and unsupported capability are recorded
   as degradation or preflight failure according to Skill policy.

The capability report has a value for every common capability: `supported`,
`unsupported`, or `unknown`. `unknown` is not equivalent to `supported`.
CodeAgent, Claude Code, and OpenCode must implement this same report shape.
An adapter may degrade only where the frozen Skill allows it; for example, a
required resumable session blocks an `unsupported` runtime rather than silently
starting a non-resumable process.

## Event Contract

Events are append-only and monotonically ordered by the existing event store.
The adapter maps provider-specific events to this F014 public vocabulary while
retaining the provider event kind only in a redacted diagnostic payload.

```text
capability_discovered -> preflight_passed -> session_created -> session_started
  -> agent_message | tool_started | tool_finished | artifact_written | checkpoint_created
  -> waiting_for_input | process_exited | process_terminated
  -> resumed -> session_started ...
  -> producer_completed -> judge_started -> judge_completed
  -> completed | failed | cancelled | timed_out
```

- `session_started` carries the disposable Process identity. A new Process after
  recovery emits another `session_started` for the same Session and increments
  its `process_generation`.
- `process_exited` and `process_terminated` are nonterminal. They carry exact
  Process identity, `process_generation`, observed time, reason, and exit code or
  signal when available. Only the owning Attempt policy decides whether to
  resume or emit `completed`, `failed`, `cancelled`, or `timed_out`.
- `checkpoint_created` is emitted only after the checkpoint file and declared
  artifact hashes are atomically durable. A projected `step_completed` cannot
  precede it.
- `artifact_written` is nonterminal, names an artifact relative to the Attempt
  root, and is emitted only once a validated declared artifact is committed.
- `waiting_for_input` and `resumed` retain the same Attempt and Session unless
  a recorded one-time clean-session recovery is required.
- `producer_completed` transitions to `PENDING_VALIDATION` when the Skill
  requires Judge; it is not `READY`.
- The terminal event is exactly one of `completed`, `failed`, `cancelled`, or
  `timed_out`. No later artifact, Judge, or completed event may mutate it.

Existing `WorkbenchTaskRunEventStore.append_once` deduplication keys are the
required mechanism for recovery projections and idempotent cancellation.

## Recovery, Cancellation, and Timeouts

| Situation | Required behavior |
|---|---|
| Process killed | Preserve committed checkpoints/artifacts; discard uncommitted staging; emit nonterminal `process_exited`; start a new Process and resume from the last valid checkpoint without first terminating the Attempt. |
| CodeTalk restart | Reconcile unfinished Attempts using frozen Invocation and checkpoint projection; never reload a mutable Draft or infer new methodology from target text. |
| Missing/incompatible/corrupt Session | Persist invalidation reason and Attempt-scoped replacement count; create one clean Session with a new ID only when the count is zero and Skill/runtime permits. A later invalidation emits typed `session_recovery_exhausted` failure. |
| Repeated cancellation | First request records cancellation intent, signals the process tree, and reaches `cancelled`; repeats return the same receipt and create no new terminal transition. |
| Post-cancel race | Artifact commit, Judge start, and completion check cancellation immediately before their durable write; reject any post-cancel transition. |
| Queue timeout | Terminal `timed_out` with `timeout_kind=queue`; no Process was started. |
| Agent timeout | Terminate the Agent process tree, then terminal `timed_out` with `timeout_kind=agent`. |
| Script timeout | Kill only the bounded script process tree, preserve prior checkpoint, and record `timeout_kind=script`. |
| Validation timeout | Do not report Producer success; record `timeout_kind=validation` and prevent `READY`. |
| Overall timeout | Cancel all active child work and record `timeout_kind=overall`; it is distinct from Agent execution time. |

The existing main recovery marks legacy incomplete runs interrupted. F014 adapts
the V3 frozen-snapshot recovery pattern so eligible Skill Attempts resume;
legacy interruption behavior must remain available until the legacy product path
is removed in Phase E.

## Runtime-Specific Degradation

All adapters expose the common contract; no adapter may hide a gap behind a
successful-looking Run.

| Runtime | Required adapter behavior |
|---|---|
| Company CodeAgent | Report discovered session/resume, tool, artifact, cancellation, model, and process capabilities before start. |
| Claude Code | Use the same report and event mapping. If its session cannot be resumed, record `resume=unsupported` and block Skills that require resume. |
| OpenCode | Record OpenCode version and the effective DeepSeek-compatible model route. Unsupported tool/cancel/resume behavior is surfaced, never assumed. |

For the real-provider F014 gate, both CodeTalk and the bounded Clowder AI
comparison use OpenCode with `deepseek/deepseek-v4-flash`; actual product LLM
review uses `deepseek-v4-flash` on the approved OpenAI-compatible DeepSeek
endpoint. These are acceptance profiles, not a replacement for capability
discovery.

## Acceptance Invariants

1. An Attempt has one and only one terminal state.
2. A Session and Process have different IDs and persisted lifetimes.
3. A committed checkpoint precedes any completion projection and includes
   output artifact hashes plus Session provenance.
4. Recovery reads only frozen Invocation/Snapshot data and committed artifacts.
5. Requested and effective model, `declared_context_refs`,
   `declared_context_window_tokens`, `requested_max_output_tokens`, limits,
   preflight result, and non-secret provenance are inspectable after completion.
6. Judge Session data is isolated from Producer transcript data.
7. A selected delivery affects only delivery packaging, never the execution
   graph or required internal artifacts.
