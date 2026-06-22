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
- Workbench provider matrix now separates CodeTalk built-in/local providers, CodeTalk index providers, CodeTalk memory providers, CodeTalk MCP bridge providers, and Agent-owned CLI providers. The same capability boundary is visible in the UI and persisted in task bundles through `provider_snapshot.json`.
- Workbench `report_render` includes artifact validation details and Evidence Memory source slices, so final reports show accepted/rejected artifacts, sha256 values, and verified source line ranges instead of only high-level task summaries.
- Agent run envelopes now include `turn_id`, `task_bundle_sha256`, and `workflow_snapshot_sha256` in execution artifacts and runtime events, so disposable Agent CLI processes still have auditable task-continuity metadata.

Operational requirement from repo `AGENTS.md`:

- The rule `mcp__fast-context__fast_context_search` is interpreted as a source-discovery preference for every exploratory code-understanding task, including workspace scope discovery, coverage entry discovery, MR/diff analysis, patch impact review, and black-box test recommendation.
- CodeTalk should attempt the fast-context path at the earliest context-discovery stage only when the MCP bridge is actually exposed to the backend process.
- If this CodeTalk process cannot see the MCP tool, the task must record `fast_context_unavailable_to_codetalk` or `fast_context_backend_bridge_unavailable` in `context_discovery_decision.json`, `degraded_retrieval.json`, and the provider matrix.
- The same instruction must still be handed to the Agent CLI in `agent_instructions.json` and the per-step task bundle, because the Agent may have its own MCP configuration and credentials inside `ccr code`, OpenCode, Claude Code, or a self-developed internal CLI.
- CodeTalk must distinguish three states in UI and artifacts: `codetalk_callable`, `agent_owned_possible`, and `unavailable`. A missing CodeTalk MCP bridge is degraded mode, not a silent fallback.
- Results from fast-context, whether produced directly by CodeTalk or indirectly by an Agent CLI artifact, are only navigation candidates until CodeTalk validates repo-local path, source extension, optional symbol location, line range, and sha256-backed source slice.
- The current desktop session exposes the instruction through `AGENTS.md`, but no callable `mcp__fast-context__fast_context_search` tool is available to this process. This is exactly the degraded path the product must make visible and non-blocking.

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
- Prepared task runs now write `memory_retrieval.json`, `source_read_chain.json`, `evidence_consumption_trajectory.json`, and `degraded_retrieval.json` so retrieval, source reads, consumption events, and provider fallback decisions are auditable outside the prompt.
- Evidence Memory hits without source slices are marked `no_source_slices` and are not treated as source evidence; retrieval remains a navigation signal until CodeTalk has a validated source slice or accepted artifact.
- Coverage black-box recommendation enrichment also carries Evidence Memory source slices into gaps and generated cases as structured evidence references.
- These source slices are the durable context chain for later Agent prompts and black-box test recommendations; raw Agent summaries remain audit material, not facts.

## Agent Run Harness

The harness launches configured Agent CLIs with a task bundle on stdin and captures:

- provider and command configuration;
- readonly environment hints;
- timeout and exit status;
- stdout/stderr with secret redaction;
- expected artifact declarations;
- validation results, including accepted artifact path, size, and sha256 in `evidence_validation.json`.
- each Agent step validation result now carries accepted/rejected artifact details, including path, sha256, size, and rejection reason, so workflow execution itself is a close-gate audit record.
- changed-file workflow outputs are materialized only when each path is backed by a repo-local file or a task patch/diff snapshot; unsupported paths are rejected per item instead of becoming Evidence Memory facts.
- user-defined output schemas are persisted in `output_schemas_by_step.json`, injected into Agent task bundles, and enforced when workflow outputs are collected; schema failures mark the output and workflow invalid.

The first implementation still relies on process timeout and prompt-level readonly rules rather than OS sandboxing. That residual risk must stay visible in docs and diagnostics.

## Clowder-AI Reuse Policy

Do not directly copy the whole clowder-ai harness or memory system into CodeTalk. Reuse the ideas, contracts, and verification habits, not the full runtime:

- adopt task-scoped structured memory and evidence ledgers;
- adopt explicit handoff/task bundles for Agent CLIs;
- adopt audit artifacts for prompts, raw outputs, and validated facts;
- adopt provider capability profiles so CodeTalk can explain what it can call directly, what the Agent CLI can call with its own credentials, and what is unavailable;
- adopt "search result is an index, not an answer" discipline: any high-confidence memory/search hit must be backed by source reads, source slices, hashes, or validated artifacts before becoming evidence;
- adopt consumption/trajectory telemetry for Evidence Memory retrieval, but use it only for navigation utility and audit, not as correctness or authority scoring;
- avoid importing unrelated persona, multi-character collaboration behavior, long-term personal memory, or broad orchestration machinery that would make CodeTalk harder to reason about.

Concrete decisions:

1. Memory architecture to borrow:
   - structured facts, source slices, hashes, provenance, accepted/rejected ledgers, and task trajectories;
   - lexical/semantic/hybrid retrieval as separate paths, with explicit degraded mode when embeddings or external providers are unavailable;
   - retrieval feedback such as "retrieved -> source read -> artifact used -> validated output" as an audit signal.
2. Memory architecture not to borrow:
   - natural-language conversation summaries as durable facts;
   - workspace-wide personal memory by default;
   - algorithmic truth/authority scoring from consumption metrics.
3. Harness architecture to borrow:
   - provider registry, health probes, launch diagnostics, stdout/stderr capture, artifact declarations, validation matrix, and close-gate style evidence accounting;
   - provider-specific command profiles such as `ccr code`, `claude`, `opencode`, and self-developed internal Agent CLIs;
   - capability profiles for MCP support, context budget, resume/session support, network requirements, and expected artifact protocol.
4. Harness architecture not to borrow:
   - direct control over the Agent's private MCP credentials;
   - hidden long-lived sessions without CodeTalk-visible task bundles;
   - trusting Agent-generated final conclusions without local validation.

This means CodeTalk stays the deterministic control plane: it prepares the task bundle, launches or delegates to the Agent CLI, records what was requested, validates returned artifacts locally, and materializes only verified evidence. The Agent CLI can still perform credentialed work that CodeTalk cannot perform, such as CodeHub MCP access for an internal MR link.

## Clowder-AI-Inspired Implementation Plan

### Phase A: Capability Profiles and Provider Matrix

Add a provider capability profile model for every context source and Agent CLI:

- CodeTalk-callable providers: local search, fast-context bridge, GitNexus, CGC, semantic library, Evidence Memory.
- Agent-owned providers: Agent CLI MCP servers such as CodeHub MCP, fast-context configured only inside `ccr code`, OpenCode MCP, or a self-developed internal Agent CLI.
- CLI providers: `ccr code`, `claude`, `opencode`, and user-defined commands.

Artifacts and UI must show:

- command discovered or missing;
- command launchable or configuration-error;
- MCP callable by CodeTalk or only by Agent CLI;
- auth boundary, for example "CodeTalk cannot fetch this MR; Agent CLI may fetch it through its configured CodeHub MCP";
- degraded fallback path used.

Tests:

- `ccr code` command is parsed as an argv vector, not a single executable name;
- missing `opencode` is unavailable without blocking;
- configured Agent-owned MCP is shown as `agent_owned`, not `codetalk_callable`;
- fast-context requested by `AGENTS.md` but unavailable to CodeTalk records a non-blocking warning.

### Phase B: Task Bundle as the Handoff Boundary

Every workflow run should produce a task bundle before Agent execution. The bundle is the only memory passed to a fresh Agent process.

Bundle contents:

- workflow id and step id;
- user inputs and uploaded files;
- repo instructions such as `AGENTS.md`;
- provider capability profile;
- context discovery decision;
- Evidence Memory hits with source slices;
- semantic test-library terms;
- accepted/rejected evidence so far;
- expected output schemas and artifact declarations;
- readonly/safety instructions.

The Agent may call its own MCP tools using its own credentials. CodeTalk does not attempt to steal or proxy those credentials. CodeTalk only validates the artifacts or JSON/stdout the Agent returns.

Tests:

- `AGENTS.md` fast-context-first instruction appears in every task bundle;
- task bundle explicitly states whether CodeTalk can call fast-context itself;
- uploaded docs and MR links are represented as structured inputs;
- task bundle excludes raw previous Agent output unless it has been converted into validated facts.

### Phase C: Evidence Memory Retrieval and Source-Read Discipline

Bring the clowder-style "search -> read -> use -> verify" chain into CodeTalk Evidence Memory.

Rules:

- search results are pointers, not facts;
- validated source slices are facts;
- semantic-library matches can influence terminology in black-box cases, but not source or entry truth;
- consumption telemetry can rerank future retrieval only after enough events and only as a navigation signal;
- authority remains local validation, user-provided material, verified source, and accepted artifacts.

Artifacts:

- `memory_retrieval.json`;
- `source_read_chain.json`;
- `evidence_consumption_trajectory.json`;
- `degraded_retrieval.json` when semantic or fast-context providers are unavailable.

Tests:

- Evidence Memory hit without source slice does not become source evidence;
- semantic library terms can appear in recommended black-box case wording;
- rejected paths enter `do_not_repeat`;
- retrieval telemetry never marks evidence as validated by itself.

### Phase D: Harness Close Gate and Artifact Validation Matrix

Adopt clowder-ai's close-gate shape for CodeTalk workflow steps.

For each workflow output, generate:

- expected artifact;
- producer step and provider;
- schema validation result;
- local path/source validation result;
- sha256, size, and storage path;
- accepted/rejected status and reason;
- materialization target, if accepted.

No workflow step should be considered complete merely because the Agent process exited successfully. It is complete only when required artifacts are either accepted or explicitly rejected with a visible reason and a configured fallback.

Tests:

- Agent exits 0 but missing artifact keeps step failed or degraded;
- invalid JSON is stored raw but not materialized;
- accepted/rejected counts are visible in API and UI preview;
- report output cites validated evidence ids, artifact hashes, rejected reasons, and source slice line ranges, not raw Agent text.

### Phase E: Agent Session and Context Budget Policy

Do not require persistent external Agent sessions in v1. Treat each Agent call as disposable, and let CodeTalk provide continuity through task bundles and Evidence Memory.

Session behavior:

- each Agent run has `run_id`, `turn_id`, provider, command, cwd, env hints, prompt/task bundle sha256, timeout, and artifacts;
- optional provider-specific resume support can be added later, but it must remain visible in diagnostics;
- context overflow triggers source-slice requests and additional turns, not free-text compression;
- raw output stays in artifacts, not in future prompts unless validated.

Tests:

- second turn receives ledger facts and source slices, not raw first-turn prose;
- context packet truncation records what was omitted and why;
- provider resume disabled still produces deterministic task continuity.

### Phase F: Custom Workflow Inputs and Agent-Owned MCP

Workflow definitions must allow file inputs and link inputs without forcing CodeTalk to fetch everything itself.

Input examples:

- requirement/design documents;
- coverage reports;
- patch plans;
- MR links;
- patch files and diffs;
- existing feature test cases for the semantic test library.

For MR links and internal systems:

- CodeTalk records the link as a structured input;
- Agent CLI receives the link and the provider profile saying which MCP it may use;
- Agent fetches through its own credentialed MCP;
- Agent returns artifacts such as `mr_summary.json`, `changed_files.json`, or `patch_diff.json`;
- CodeTalk validates any referenced repo files and hashes locally before materialization.

Tests:

- workflow can define required file input and optional link input;
- Agent-owned CodeHub MCP capability appears in bundle and UI;
- MR artifacts from Agent are rejected if changed files do not exist in the local repo or patch snapshot;
- user-defined workflow output schemas are enforced.

### Phase G: Fast-Context and AGENTS.md Compliance

The root `AGENTS.md` rule says exploratory code understanding should prefer `mcp__fast-context__fast_context_search`. CodeTalk must preserve this as a first-class plan item:

- detect repo and nested `AGENTS.md` files before preparing any Agent task bundle;
- parse fast-context-first instructions as structured policy, not as prompt-only prose;
- if the fast-context MCP tool is exposed to CodeTalk, call it before local/index/Agent discovery;
- if it is not exposed, record `fast_context_unavailable_to_codetalk` or `fast_context_backend_bridge_unavailable` and continue;
- if the Agent CLI may have fast-context or CodeHub MCP inside its own runtime, mark it as `agent_owned_possible`;
- include the exact AGENTS instruction, provider decision, and fallback chain in task artifacts;
- include the decision in every Agent turn envelope so fresh Agent CLI processes do not lose the rule after context compaction or restart;
- never silently treat a missing MCP as if the instruction did not exist.

Tests:

- fast-context unavailable does not block scope resolution;
- unavailable warning appears in task preparation artifacts and provider matrix;
- Agent task bundle still includes the fast-context-first instruction;
- local validation remains mandatory for fast-context and Agent results.
- context-discovery artifacts distinguish CodeTalk-callable fast-context from Agent-owned fast-context or CodeHub MCP.
- coverage and black-box recommendation workflows preserve the same AGENTS.md instruction path, not only module-scope workflows.

## Next Implementation Phases

1. Finish the visible provider capability matrix, separating CodeTalk-callable MCP providers, Agent-owned MCP providers, local deterministic tools, GitNexus, CGC, and semantic memory.
2. Add or harden provider diagnostics for `fast-context` MCP, `ccr code`, Claude Code, OpenCode, and user-defined internal Agent CLIs.
3. Complete Agent Run Harness routing for workflow execution, including task bundles, artifact declarations, validation matrix, accepted/rejected evidence, and materialization.
4. Extend Evidence Memory retrieval so black-box test generation can use semantic test-library terminology, validated source facts, source slices, MR facts, and coverage facts.
5. Add source-read and consumption trajectory artifacts so the UI can answer "which search hit was actually read and used".
6. Add UI views for task bundle, provider warnings, provider ownership, validated evidence, rejected evidence, generated artifacts, and source slices.
7. Keep AGENTS.md and other repo-local agent instructions visible in task bundles and debug artifacts so external CLI behavior is auditable.
8. Add custom workflow input/output schema editing, including file inputs, link inputs, user-defined artifacts, and Agent-owned MCP hints.
9. Add regression coverage that a repo `AGENTS.md` fast-context-first rule is captured in the task bundle, appears in execution artifacts, and degrades cleanly when the MCP bridge is unavailable.
10. Add regression coverage that Agent-owned MCP links, such as internal MR links, are passed to the Agent CLI but still require CodeTalk-local artifact and source validation before evidence materialization.
