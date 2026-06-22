# CodeTalk Agent Workbench Roadmap

## Goal

CodeTalk should become the UI, configuration, workflow, memory, validation, and audit layer for analysis tasks. Agent CLIs such as `ccr code`, Claude Code, OpenCode, and internal Agent tools perform code exploration and reasoning, while CodeTalk keeps the task contract, evidence ledger, artifact validation, and user-facing traceability.

## Architecture Direction

1. CodeTalk owns workspace setup, workflow definitions, task creation, input files, semantic test libraries, and evidence memory.
2. Agent CLIs own credentialed external access such as internal MR links, CodeHub MCP calls, and private toolchains that CodeTalk cannot directly call.
3. CodeTalk does not trust raw Agent conclusions. It validates files, paths, symbols, entries, artifacts, and source slices locally before materializing evidence.
4. Agent output must be persisted as auditable artifacts, but only validated structured evidence enters reports and test recommendations.
5. Workflow presets are built in, but users can install, copy, edit, or create their own workflows with custom inputs, steps, and outputs.

## Context Discovery Order

For any code-understanding workflow, CodeTalk should assemble context in this order:

1. `fast-context` MCP search, when the MCP tool is available in the runtime.
2. Local deterministic search: `rg`, `git grep`, `git ls-files`, and source-slice readers.
3. GitNexus and CGC indexes, when healthy.
4. External Agent CLI discovery through the Agent Run Harness.

Rules:

- `fast-context` is a preferred first-pass code locator, not a hard dependency.
- Repo `AGENTS.md` can make this preference explicit. When it says exploratory code understanding should use `mcp__fast-context__fast_context_search` first, task preparation must preserve that instruction as structured task context rather than treating it as an informal note.
- If `fast-context` is unavailable, the task continues and records a non-blocking provider warning.
- Search results from all providers enter the evidence ledger with provider, command, confidence, and validation status.
- Agent CLI output never bypasses CodeTalk validation.

Current implementation note:

- `fast-context` is registered as a diagnostic adapter and an optional source-discovery provider.
- Backend scope resolution only invokes it when `fast_context_backend_bridge_enabled=true`; by default CodeTalk reports the bridge as unavailable without blocking analysis.
- Candidate files returned by this provider are normalized through the same local source-file validation path as external Agent candidates.
- Workbench task preparation reads applicable repo `AGENTS.md` files, stores them in `agent_instructions.json`, and injects them into every Agent task bundle. These instructions can require fast-context-first exploration while CodeTalk still records unavailable providers as non-blocking warnings.
- Every prepared task bundle and execution audit should make the fast-context decision explicit: requested by repo instructions, callable by CodeTalk or not, fallback path used, and whether the external Agent CLI may satisfy the same instruction through its own MCP credentials.
- Workbench artifact preview renders `evidence_validation.json` with accepted/rejected counts and accepted artifact sha256 snippets, so validation evidence is visible without reading raw JSON first.

## Repo Agent Instructions

CodeTalk must treat repo-local agent instructions as task input, not as hidden process behavior.

`AGENTS.md` handling:

- read the workspace root `AGENTS.md` during task preparation;
- read nested `AGENTS.md` files implied by user-provided path hints and uploaded file locations;
- store exact instruction files with path, size, sha256, truncation flag, and content in `agent_instructions.json`;
- inject the same instruction payload into every Agent CLI task bundle;
- expose the instruction payload in task artifacts so users can audit what the Agent was asked to obey.
- derive a `context_discovery_decision` from those instructions before execution, including whether `fast-context` was requested, whether CodeTalk can call it directly, which fallback providers are used, and whether the external Agent CLI may satisfy the same MCP requirement through its own configured credentials.

The fast-context rule from `AGENTS.md` is interpreted as:

- CodeTalk should prefer `fast-context` MCP for exploratory source discovery when the MCP bridge is available to CodeTalk.
- If CodeTalk cannot call the MCP directly, it records `fast-context` as unavailable or bridge-disabled and continues with local search, indexes, and Agent CLI execution.
- Agent CLIs may still call their own MCP tools, including fast-context-like or internal CodeHub MCP tools, using their own credentials and configuration.
- Agent CLI results still enter CodeTalk through artifacts or stdout and must pass local path, source, hash, and schema validation before becoming evidence.

This keeps the user's repo instruction visible and auditable while avoiding a false assumption that every CodeTalk deployment can directly access the same MCP tools as the Agent CLI.

## Workflow Presets

Initial built-in presets:

- `module_analysis`: full module or feature analysis.
- `resource_leak_hunt`: lightweight resource leak, cleanup, and abnormal branch hunt.
- `mr_blackbox_test`: MR-driven black-box test design where Agent CLI fetches MR context through its own MCP credentials.
- `patch_impact_review`: patch plan or diff impact analysis, before/after flow changes, affected scope, and test recommendations.

Each preset is an editable workflow definition. Installing a preset copies it into the workflow registry so users can customize it without changing the built-in template.

Current validation note:

- Workflow definitions reject duplicate input, step, and output ids.
- Plain output `from` / `source` references must point to an existing step; templated references such as `{{steps.render.output}}` remain allowed for compatibility.

## Evidence Memory

Evidence Memory stores structured facts rather than natural-language summaries:

- validated and rejected files;
- source files and symbols from verified `source_scope.json` workflow outputs;
- evidence cards from verified `evidence_cards.json` workflow outputs;
- changed files and MR artifacts;
- coverage gaps from verified `uncovered_functions.json` workflow outputs;
- symbols, entries, source slices, and hashes;
- provider status and command/runtime history;
- validation errors and rejection reasons.

Natural-language summaries are allowed as display material, but they are not the source of truth for later analysis.

Current implementation note:

- Verified `source_scope.json`, `evidence_cards.json`, and repo-local `uncovered_functions.json` outputs create Evidence Memory facts only after local repo/source validation.
- Materialized source-file, evidence-card, and coverage-gap facts get source slices with repo-relative path, line range, sha256, and local excerpt, exposed through the memory source-slices API.
- Prepared task bundles include source slices for retrieved Evidence Memory facts so the next Agent CLI turn sees structured code context instead of a lossy summary.
- These source slices are the durable context chain for later Agent prompts and black-box test recommendations; raw Agent summaries remain audit material, not facts.

## Agent Run Harness

The harness launches configured Agent CLIs with a task bundle on stdin and captures:

- provider and command configuration;
- readonly environment hints;
- timeout and exit status;
- stdout/stderr with secret redaction;
- expected artifact declarations;
- validation results, including accepted artifact path, size, and sha256 in `evidence_validation.json`.

The first implementation still relies on process timeout and prompt-level readonly rules rather than OS sandboxing. That residual risk must stay visible in docs and diagnostics.

## Clowder-AI Reuse Policy

Do not directly copy the whole clowder-ai harness or memory system into CodeTalk. Reuse the ideas, not the full runtime:

- adopt task-scoped structured memory and evidence ledgers;
- adopt explicit handoff/task bundles for Agent CLIs;
- adopt audit artifacts for prompts, raw outputs, and validated facts;
- avoid importing unrelated persona, long-term workspace memory, or broad orchestration machinery that would make CodeTalk harder to reason about.

## Next Implementation Phases

1. Add provider diagnostics for `fast-context` MCP availability and record it in tool status.
2. Add a `fast-context` source discovery provider that feeds the same candidate validation pipeline as local search, GitNexus, CGC, and external Agent CLI.
3. Route workflow execution through the Agent Run Harness end to end, including artifact validation and materialization.
4. Extend Evidence Memory retrieval so black-box test generation can use semantic test-library terminology and validated source/MR facts.
5. Add UI views for task bundle, provider warnings, validated evidence, rejected evidence, and generated artifacts.
6. Keep AGENTS.md and other repo-local agent instructions visible in task bundles and debug artifacts so external CLI behavior is auditable.
7. Add a visible provider capability matrix that separates CodeTalk-callable MCP providers from Agent-owned MCP providers, so internal deployments can explain why CodeTalk cannot call a tool while `ccr code`, OpenCode, or a self-developed Agent CLI can.
8. Add regression coverage that a repo `AGENTS.md` fast-context-first rule is captured in the task bundle, appears in execution artifacts, and degrades cleanly when the MCP bridge is unavailable.
