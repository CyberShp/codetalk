---
feature_ids: [agent-harness-v3, zero-public-egress]
topics: [sdk, poc, offline]
doc_kind: decision-record
created: 2026-07-23
updated: 2026-07-27
status: superseded_by_adr_024
---

# SDK Runtime POC Gate

## Decision

CodeTalk may download and evaluate vendor SDKs in an isolated developer POC environment.
That development activity is not CodeTalk product runtime traffic. Production and intranet
deployments must install only approved, pinned vendor bundles from their internal artifact
source and must deny every non-approved autonomous destination at runtime. A configured and
deployment-approved inference endpoint is a narrow exception even when its address looks public.
The current durable runtime
remains CodeTalk's existing local workflow runner behind the internal Harness Facade until
the POC comparison selects a replacement.

## Current evidence

On 2026-07-23, the Python runtime, pip cache, and `/Volumes/Media` were checked for
`claude_agent_sdk`, OpenAI `agents`, `langgraph`, Microsoft Agent Framework and their
wheel/source bundles. None were present. This blocks only an offline deployment POC; it
does not prohibit a separately isolated developer-machine download for SDK evaluation.

## Required offline POC matrix

Each supplied SDK bundle, or a developer-installed SDK with its resolved version frozen
into a later vendor bundle, must be tested in an isolated adapter process against the same
fixture workspace and frozen RunSnapshot: capability probe, start, structured events,
cancellation, recovery, artifact collection, and public-egress traffic capture. OpenAI
trace upload, LangSmith, hosted MCP, telemetry, update checks and remote studio features
must be disabled before the process is launched.

No candidate becomes the durable stage runtime unless it passes the matrix and improves
recovery/event semantics over the existing runner without creating a second workflow truth.

## Developer-machine POC record

The following packages were installed only into separate developer POC virtual environments
under `/Volumes/Media/codetalk-sdk-poc/`. They are not CodeTalk production dependencies,
are not referenced from the application startup path, and must be converted into an approved
internal vendor bundle before any deployment evaluation.

| Candidate | Version | Isolated POC result | Runtime egress finding |
| --- | --- | --- | --- |
| Claude Agent SDK | `0.2.125` | Import and `ClaudeSDKClient` preflight work; a deliberately missing CLI exits with `CLINotFoundError` without a network attempt. A real source-task session remains pending an authenticated CLI behind the deployment egress policy. | SDK wraps a local CLI; product must use sanitized environment and OS/deployment egress control. |
| OpenAI Agents SDK | `0.18.3` | `Runner.run()` completed against a real loopback-only OpenAI-compatible `/v1/chat/completions` fixture and produced the expected final output. | Tracing is enabled by default. POC explicitly called `set_tracing_disabled(True)` and set `RunConfig(tracing_disabled=True)`; the provider reported `_disabled=True`. |
| Microsoft Agent Framework Core | `1.12.0` | Two real local executors (`source-evidence -> publish`) completed with output `KDPS`. | Core includes OpenTelemetry APIs; no exporter or hosted provider is accepted in the CodeTalk intranet path. |
| LangGraph | `1.2.9` | A compiled local `StateGraph` completed a state transition from `41` to `42`. | Its dependency tree includes LangSmith; it cannot enter production until tracing is explicitly disabled and traffic capture proves zero outbound trace traffic. |

The first broad, shared virtual environment failed to resolve all four SDK dependency trees
cleanly. Each candidate therefore has its own Adapter-process boundary in POC. This is an
important product constraint: CodeTalk must never load several vendor SDKs into its main
backend interpreter merely for provider selection.

## Current selection

This POC gate has been concluded by
[ADR-024: Retain the Local Durable Stage Runtime](adr-024-durable-stage-runtime-selection.md).
CodeTalk's existing local Workflow Runner plus `AgentHarnessFacade` is the sole Durable Stage
Runtime for V3. It owns `WorkflowVersion`, `RunSnapshot`, task state, events and Artifact
Contract; candidate SDKs remain behind isolated Provider Adapters and may not create a second
task ID, workflow state or Artifact Store.

The POC comparison remains useful as admission evidence for a future Adapter, but it is not a
pending runtime-selection process. A candidate must still pass ADR-024's six production gates:
approved offline bundle/SBOM, sanitized telemetry-free launch, frozen Harness contract, OS
sandbox, deployment traffic capture, and a real SPDK task with cancellation/recovery and
artifact/quality evidence. Until then no SDK is a product startup dependency.
