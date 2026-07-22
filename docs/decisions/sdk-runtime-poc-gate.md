---
feature_ids: [agent-harness-v3, zero-public-egress]
topics: [sdk, poc, offline]
doc_kind: decision-record
created: 2026-07-23
---

# SDK Runtime POC Gate

## Decision

CodeTalk may download and evaluate vendor SDKs in an isolated developer POC environment.
That development activity is not CodeTalk product runtime traffic. Production and intranet
deployments must install only approved, pinned vendor bundles from their internal artifact
source and must deny every public destination at runtime. The current durable runtime
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

No Durable Stage Runtime is selected yet. CodeTalk keeps its existing local workflow runner
as the durable runtime while the Provider Adapter POCs continue. The later selection compares
real SPDK sessions, cancellation, resume, event fidelity, artifact collection, security
controls, dependency/SBOM footprint and migration cost; it will choose at most one framework
or formally retain the current runner.
