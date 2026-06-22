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
- If `fast-context` is unavailable, the task continues and records a non-blocking provider warning.
- Search results from all providers enter the evidence ledger with provider, command, confidence, and validation status.
- Agent CLI output never bypasses CodeTalk validation.

## Workflow Presets

Initial built-in presets:

- `module_analysis`: full module or feature analysis.
- `resource_leak_hunt`: lightweight resource leak, cleanup, and abnormal branch hunt.
- `mr_blackbox_test`: MR-driven black-box test design where Agent CLI fetches MR context through its own MCP credentials.
- `patch_impact_review`: patch plan or diff impact analysis, before/after flow changes, affected scope, and test recommendations.

Each preset is an editable workflow definition. Installing a preset copies it into the workflow registry so users can customize it without changing the built-in template.

## Evidence Memory

Evidence Memory stores structured facts rather than natural-language summaries:

- validated and rejected files;
- changed files and MR artifacts;
- symbols, entries, source slices, and hashes;
- provider status and command/runtime history;
- validation errors and rejection reasons.

Natural-language summaries are allowed as display material, but they are not the source of truth for later analysis.

## Agent Run Harness

The harness launches configured Agent CLIs with a task bundle on stdin and captures:

- provider and command configuration;
- readonly environment hints;
- timeout and exit status;
- stdout/stderr with secret redaction;
- expected artifact declarations;
- validation results.

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
