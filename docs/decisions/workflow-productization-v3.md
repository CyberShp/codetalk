---
feature_ids: [workbench-v3, agent-harness, workflow-execution-profile]
topics: [workflow, run-snapshot, agent-harness, execution-profile]
doc_kind: decision-record
created: 2026-07-23
---

# Workflow Productization V3 Decisions

## D021: one graph, two execution policies

Every workflow version describes one reusable graph, its named inputs, agent/MCP/skill
bindings, gates, and artifact contract. `rapid` and `deep` are execution policies, not
separate workflows or hidden prompt variants.

`rapid` targets bounded source-backed analysis in 10-25 minutes and uses at most one
helper subagent. `deep` targets a complete test-design delivery in 45-90 minutes and
permits up to four specialized helpers. A complex run may exceed these estimates only
when its snapshot explicitly records the reason and new estimate; it must never silently
degrade output coverage to meet a timer.

The selected policy is copied to the RunSnapshot, `task_bundle.json`, each Agent handoff,
and `execution_profile.json`. A retry inherits the parent snapshot and cannot switch
policy; users create a new attempt to change policy.

## D022: workflow versions are contracts; runs are evidence

Published workflow definitions remain immutable. At run creation CodeTalk freezes:

- WorkflowVersion and compiled execution plan.
- Named input bindings and their ingested copies or hashes.
- Workspace revision and source evidence selection.
- ExecutionProfile and its bounded resource policy.
- Agent provider, MCP capability references, skills, artifact specifications, and gates.

The cockpit is therefore a projection of an immutable snapshot, not a reconstruction from
current settings. This prevents a settings edit, preset upgrade, or retry from changing
the meaning of a historical delivery.

## D023: one durable runtime behind an internal facade

The product exposes one internal `AgentHarnessFacade` contract: plan, start, stream
semantic events, cancel, resume where supported, collect ArtifactContract outputs, and
return a normalized diagnostic. Provider-specific CLI or SDK details stay behind adapters.

Claude Agent SDK, OpenAI Agents SDK, and Microsoft Agent Framework/LangGraph are assessed
only through isolated offline POCs. A POC may use a local fake endpoint and fixture tool
server, but may not become a production dependency until it proves approved-purpose transport,
structured events, cancellation, artifact collection, and session recovery. The current
durable runtime remains CodeTalk's local harness until one candidate is approved in a
separate ADR.

## D024: quality is claim-based, not prompt-based

Every factual technical statement is a claim with an evidence anchor. Deterministic
validators verify source quotes, files, lines, symbols, constants, and schemas. An
independent evidence judge handles behavioral support/contradiction/insufficiency. Raw
output and a single quality score never constitute delivery readiness.

The cockpit shows separate structure, factual-verification, executable-harness, and
coverage-disposition statuses. A blocked result remains downloadable as a diagnostic
artifact but is never labelled as an accepted delivery.

## D025: output is an ArtifactContract, not terminal prose

Outputs are named, typed, versioned artifact declarations. A basic source-to-test workflow
must declare at least the source-flow report, evidence cards, SFMEA, black-box cases, and
test strategy; optional mind-map output is a first-class artifact rather than an embedded
JSON blob. Agent prose is a readable summary, while files remain the durable deliverables.
