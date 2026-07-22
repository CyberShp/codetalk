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
