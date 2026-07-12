---
feature_ids: [F002]
topics: [ai-thread, agent-runtime, clowder-parity]
doc_kind: feature
created: 2026-07-05
status: done
completed: 2026-07-12
---

# F002 Clowder-style AI Agent Parity

## Goal

Make CodeTalk AI threads behave like Clowder AI in agent lifecycle management, excluding visual style differences.

## TODO

- [x] Agent spawn lifecycle: show structured lifecycle stages instead of raw CLI initialization text.
- [x] Session continuity: make resume-capable agent runtimes the default and clearly show whether a run is resumed or fresh.
- [x] Process visibility: keep thinking/CLI/process details visible as collapsed diagnostics with live progress, timing, cancellation, and failure cues.
- [x] Artifact-first long outputs: route SFMEA, test design, black-box cases, and other long structured results to downloadable artifacts instead of dumping them into the terminal/chat body.
- [x] Source-first execution: when a workspace is selected, prefer GitNexus/CGC/source evidence unless the user explicitly opts out.
- [x] Activity-aware timeout: stdout/stderr/event activity renews the timeout window; silent execution is stopped with an actionable message and a one-hour hard safety cap.
- [x] Cross-executor invocation identity: capability manifests retain `agent-runtime:<runtime_id>` for persisted Agent runtimes.
- [x] Real comparison baseline: provision a local OpenCode member and run the exact same SPDK iSCSI Login task through both products using real browser interaction.
- [x] Multi-file delivery: expose accepted per-file artifacts, manifest metadata, individual downloads and ZIP download while chat remains compact.
- [x] Long builtin task staging: automatically split comprehensive test work into source analysis, flow, SFMEA and black-box stages.
- [x] Audited execution boundary: apply macOS sandbox-exec/Linux bubblewrap policies with explicit workspace read and artifact write scopes.

## Acceptance Criteria

- AI thread final answer contains only the user-facing answer, not agent boot banners, shell prompts, raw command transcripts, or source-tail noise.
- Right-side agent status exposes lifecycle stage, session mode, run id, elapsed time, event counts, and collapsed process details.
- Resume-capable runtimes use session continuity by default without requiring users to configure end markers.
- Long structured outputs are exposed as files/artifacts with download controls, while chat shows a compact summary and links.
- Workspace-scoped prompts include source-evidence instructions by default and preserve multiline user prompts.
- NGA/raw stdin, Claude, Codex, OpenCode and builtin LLM transports preserve the complete multiline task; compound `gitnexus+cgc` profiles use CodeTalk prefetch when the executor cannot call MCP directly.
- E2E coverage protects Clowder-like lifecycle display, process collapse, artifact-first output, and source-first prompting.

## Validation

- Same-task prompt: SPDK iSCSI Login PDU -> Full Feature Phase, CHAP failure recovery, source evidence, tester understanding, SFMEA and external-observation-only black-box cases.
- Clowder AI: native production web on `3403`, API on `3404`, memory storage, OpenCode `opencode/big-pickle`. Real UI created the SPDK project thread and submitted the full prompt. It performed 16 source/tool operations and completed in 45 seconds. Lifecycle, stop, queued input and collapsed CLI details worked. Its final user-visible body was empty and it produced no independent deliverables; analysis remained in the private CLI bubble.
- CodeTalk candidate: frontend `3503`, API `3504`, isolated copy of the configured database. Real UI selected the SPDK workspace and builtin model, submitted the same prompt, displayed four automatic stages and completed in 1 minute 23 seconds.
- CodeTalk delivery: `sfmea.json` (15 rows), `black_box_cases.json` (10 cases, all eight required dimensions), `business_flow.md`, `artifact_manifest.json`, per-file downloads and ZIP. Every declared artifact was accepted and carried size and sha256 metadata.
- GPT rubric: 88/100. Sampled source positions and seven mapped SPDK test paths exist; no P0/P1 hallucination or black-box boundary violation was found.
- Regression: Workbench Playwright `17 passed`; focused backend AI suites `116 passed`; sandbox/runtime suites `151 passed`; external discovery `374 passed, 1 skipped`; GitNexus adapter/API `58 passed`; production frontend build and TypeScript passed before final full gates.

## Closure

F002 is complete. “Parity” means lifecycle semantics and user-facing separation of answer/process/artifacts, not reproducing Clowder defects or visual styling. The real comparison proves CodeTalk keeps the useful lifecycle behavior and provides stronger artifact-first delivery for this task.
