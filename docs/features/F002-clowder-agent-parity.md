---
feature_ids: [F002]
topics: [ai-thread, agent-runtime, clowder-parity]
doc_kind: feature
created: 2026-07-05
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
- [x] Real comparison baseline: compare the deployed Clowder AI and CodeTalk surfaces for lifecycle/process/artifact behavior; live same-task Clowder execution requires an available Clowder member.

## Acceptance Criteria

- AI thread final answer contains only the user-facing answer, not agent boot banners, shell prompts, raw command transcripts, or source-tail noise.
- Right-side agent status exposes lifecycle stage, session mode, run id, elapsed time, event counts, and collapsed process details.
- Resume-capable runtimes use session continuity by default without requiring users to configure end markers.
- Long structured outputs are exposed as files/artifacts with download controls, while chat shows a compact summary and links.
- Workspace-scoped prompts include source-evidence instructions by default and preserve multiline user prompts.
- E2E coverage protects Clowder-like lifecycle display, process collapse, artifact-first output, and source-first prompting.

## Validation

- Backend source/artifact semantics: `7 passed` for GitNexus/CGC priority, source-first prompt, artifact contract, history artifact continuity, Claude stream pollution isolation.
- Frontend lifecycle/artifact E2E: `5 passed` for collapsed process diagnostics, Clowder-style lifecycle status, artifact-first delivery, friendly process summaries, and long process history retention.
- Runtime comparison baseline: Clowder `3013` and API `3014` returned 200; CodeTalk worktree `3103/ai` returned 200. Clowder currently reports no available member, so live same-task SPDK execution is blocked until a member is created. The visible Clowder side panel keeps lifecycle/process/session controls separate; CodeTalk now exposes equivalent non-style states in `Agent 状态` and keeps final answers/artifacts separated from process output.
