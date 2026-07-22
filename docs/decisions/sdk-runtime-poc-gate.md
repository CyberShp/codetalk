---
feature_ids: [agent-harness-v3, zero-public-egress]
topics: [sdk, poc, offline]
doc_kind: decision-record
created: 2026-07-23
---

# SDK Runtime POC Gate

## Decision

CodeTalk will not add a network-installed SDK to production or select a durable stage
runtime until an approved offline package bundle is available. The current durable runtime
remains CodeTalk's existing local workflow runner behind the internal Harness Facade.

## Current evidence

On 2026-07-23, the Python runtime, pip cache, and `/Volumes/Media` were checked for
`claude_agent_sdk`, OpenAI `agents`, `langgraph`, Microsoft Agent Framework and their
wheel/source bundles. None were present. Installing from public PyPI would violate the
V3 intranet policy, so no install was attempted.

## Required offline POC matrix

Each supplied SDK bundle must be tested in an isolated adapter process against the same
fixture workspace and frozen RunSnapshot: capability probe, start, structured events,
cancellation, recovery, artifact collection, and public-egress traffic capture. OpenAI
trace upload, LangSmith, hosted MCP, telemetry, update checks and remote studio features
must be disabled before the process is launched.

No candidate becomes the durable stage runtime unless it passes the matrix and improves
recovery/event semantics over the existing runner without creating a second workflow truth.

