---
feature_ids: [workflow-productization-v3, agent-harness-v3, zero-public-egress]
topics: [adr, durable-runtime, sdk, offline, security]
doc_kind: architecture-decision-record
created: 2026-07-25
status: accepted
---

# ADR-024: Retain the Local Durable Stage Runtime

## Context

CodeTalk needs one durable owner for `WorkflowVersion`, `RunSnapshot`, stage
state, cancellation, recovery, artifacts, and user-visible task status.  The
product must not create a second workflow truth when a provider changes from a
CLI to an SDK.  It also runs in controlled intranets where an SDK's telemetry,
trace, hosted MCP, update check, or plugin discovery is unacceptable unless it
is explicitly approved for the user-triggered task.

The isolated POCs in `sdk-offline-poc-2026-07-24.md` established that Claude
Agent SDK, OpenAI Agents SDK, Microsoft Agent Framework, and LangGraph can be
imported or execute a deterministic local probe with socket access denied. They
did not establish production-safe model sessions, deployment traffic capture,
or a complete cancellation/resume/artifact advantage over the current runner.

## Decision

CodeTalk retains its existing local Workflow Runner plus `AgentHarnessFacade`
as the **only Durable Stage Runtime** for V3.

- `WorkflowVersion`, `RunSnapshot`, `WorkbenchTaskRun`, event sequence and
  Artifact Contract V3 remain the sole public and persisted truth sources.
- Provider CLIs and any future Claude/OpenAI/Microsoft/LangGraph integration
  run only behind a provider Adapter. They return normalized semantic events
  and artifact references; they cannot create task IDs, persisted workflow
  state, or an alternative artifact store.
- No vendor SDK becomes a product dependency or backend startup import from
  this ADR. The existing local runner is not a provisional parallel runtime;
  it is the selected durable runtime until a superseding ADR is accepted.

## Security Boundary

Every Adapter candidate must satisfy all of the following before a production
integration proposal can be reviewed:

1. Install from an approved offline bundle with version, SHA-256, transitive
   license record and SBOM.
2. Start in a sanitized, generated configuration that disables vendor tracing,
   telemetry, update checks, plugin discovery, hosted MCP, remote studio and
   proxy inheritance unless a specific approved purpose requires it.
3. Use CodeTalk's frozen RunSnapshot inputs and the `AgentHarnessFacade`
   cancellation/artifact contract.
4. Run inside the required OS process sandbox in intranet mode; a missing
   sandbox or unapproved Agent route fails closed before spawn.
5. Pass a real traffic capture showing that only a user-triggered Provider
   Adapter request reaches the declared narrow API route. All autonomous
   destinations must be absent.
6. Demonstrate a real SPDK task with readiness, source consumption, semantic
   events, cancellation/recovery semantics, artifact materialization and the
   final quality gate.

An approved inference endpoint is identified by deployment-approved purpose,
hostname and narrow API route, not by whether its IP address resembles a
private address. This does not authorize SDK-owned endpoints.

## Consequences

- The V3 runtime has one recoverable task/event/artifact lifecycle and does
  not need to migrate user data when adapters are evaluated.
- Adapter development is slower by design: it cannot use a successful import,
  loopback fixture, or command-exists probe as evidence of production
  readiness.
- The current local runner remains responsible for parallel stages, reuse,
  time budgets, quality repair and delivery governance.

## Evidence and Review Trigger

- Isolated POC inventory: `sdk-offline-poc-2026-07-24.md`.
- Existing workflow/harness decision: `workflow-productization-v3.md` D023.
- Egress controls and remaining capture requirements:
  `../security/zero-public-egress-threat-model.md` and
  `../security/zero-public-egress-verification.md`.

This ADR may be superseded only after one candidate completes the six security
and runtime gates above and an independent review finds a material benefit
without creating a second workflow semantic.
