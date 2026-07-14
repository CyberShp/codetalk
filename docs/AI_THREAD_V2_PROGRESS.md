---
feature_ids:
  - AI_THREAD_V2_INTEGRATION
topics:
  - ai-thread
  - workbench-v2
doc_kind: progress
created: 2026-07-14
---

# AI Thread V2 Integration Progress

## Baseline

- Required review baseline: `1b87bb73e2016f542eba0a141d032db6ff21287d`.
- Implementation baseline: `831493ecd90e62291729a9568ced0b0b732edfce` (`origin/feat`).
- Delta reviewed: workflow selection, canvas drag fix, and single-release-preset changes.
- Worktree: `/Volumes/Media/codetalk-ai-thread-v2`.
- Branch: `codex/ai-thread-v2-integration`.
- Main repository user changes are untouched (`.agents/` remains untracked there).

## Phase Status

| Phase | Status | Evidence |
|---|---|---|
| Contract and current-state review | Complete | `docs/AI_THREAD_V2_INTEGRATION_PLAN.md` |
| Snapshot and link migration | Complete | Idempotent migration, immutable/legacy tests |
| AI -> Task Draft | Complete | Published-version ownership tests and six-step restore |
| Run -> AI | Complete | Create-or-open link, redacted context, reciprocal UI links |
| Atomic queue and capacity | Complete for AI spawn | Concurrent POST, duplicate kick, failure continuation, coordinator limits |
| Run Cards, timeline, real actions | Complete | Batch historical runs, paired tools, honest suggested follow-ups |
| Real E2E and release gate | Complete except independent review/push | Real browser chain, responsive screenshots, build and regression gates are green |

## Current State Machine

Existing AI queue state is `queued -> running -> completed/failed/cancelled`, but claiming is not atomic. Target state keeps the same public states and adds a transactional queued-to-running claim plus global/provider capacity waiting metadata.

## Compatibility Rules

- Legacy `selected_workflow_id` is readable.
- New bindings also persist `selected_workflow_version_id` and an immutable snapshot.
- Legacy runs display unrecorded snapshot fields; current conversation configuration is never substituted.
- Existing `/workbench` rollback routes remain available for one release but are removed from new V2 primary actions.
- No history, artifact, message, run, workflow version, or task is deleted.

## Verification Log

- `python3.11 -m pytest -q tests/test_ai_thread_v2_integration.py tests/test_ai_conversations.py tests/test_database_init.py`: `150 passed` before the final concurrency additions.
- `python3.11 -m pytest -q tests/test_ai_thread_v2_integration.py -k 'concurrent_message_posts or spawn_failure_advances or duplicate_queue_kick'`: `3 passed`.
- Relevant AI + Workbench Task/Workflow/Scheduler/API/artifact regression command: `327 passed in 99.75s` in the final fresh gate.
- `npm run lint -- --max-warnings=0`: passed.
- `./node_modules/.bin/tsc --noEmit --pretty false`: passed.
- `npm run build`: passed; all production Next.js routes compiled and type-checked.
- `frontend/e2e/ai-thread-v2-integration-real.spec.ts`: `1 passed in 20.3s` with a real two-Agent DAG and no request mocks.
- Integration, Workbench task wizard, run cockpit, workflow designer, and bounded AI layout browser group: `7 passed in 43.6s`; retry and source-first Agent regressions are also green in focused reruns.
- `git diff --check`: passed before the final review gate.
- Responsive evidence: `frontend/output/playwright/ai-thread-v2/run-cockpit-desktop.png` at `1440x900` and `frontend/output/playwright/ai-thread-v2/linked-ai-thread-mobile.png` at `390x844`; no horizontal overflow.

## Implemented Product Chain

1. A new AI binding freezes a published Workflow Version and labels ordinary answers as workflow-constrained answers, not DAG execution.
2. `POST /api/ai/conversations/{conversation_id}/task-drafts` creates a V2 Task Draft from server-owned workflow data and records its AI origin.
3. The six-step Task Wizard restores the frozen workflow and workspace, locks those facts, and starts a real immutable Attempt through the existing compiled plan and scheduler.
4. Run Cockpit explains nodes, received inputs, dependencies, tools, outputs, failure reuse/retry scope, quality, and deliverables.
5. `POST /api/ai/conversations/from-task-run/{task_run_id}` creates or reopens a redacted Task Run discussion thread; Task Detail and AI Thread link back in both directions.
6. Each assistant message renders its own immutable Run snapshot, public timeline, evidence, artifacts, and quality summary.

## Compatibility Fixes Found During Gates

- `AIWorkbenchLinkStore` now self-initializes its additive table for isolated API startup and rolling upgrades.
- Optional synchronous Agent runtime discovery returns an empty capability set when its SQLite database has not yet been created.
- Existing Run UI `inputs` payload shape remains unchanged; actual received values are exposed in the additive `received_inputs` field.
- Large Run UI summaries are no longer duplicated into machine rerun artifacts, preserving stable artifact previews and replay evidence.
- Workflow-declared custom output paths are classified as public deliverables from frozen workflow output contracts; diagnostics and undeclared files remain non-deliverable.
- Run-linked AI threads now turn public manifest deliverables into bounded, path-safe evidence references and include their redacted excerpts in external Agent prompts.
- AI retry E2E follows the dedicated retry endpoint and verifies the immutable input message is reused instead of duplicating the user's message.
- Deterministic macOS browser fixtures live inside their temporary workspace so project-scoped Agent subprocesses can execute them without escaping the workspace sandbox.

## Real Browser Acceptance Chain

1. Create a temporary source workspace through the UI.
2. Open an AI thread bound to a published two-Agent Workflow Version.
3. Create a Task Draft from the AI thread, preserving the fixed workflow and workspace.
4. Enter the exact analysis target, confirm provider, MCP, Skills, and expected output in the six-step wizard.
5. Start the task through the browser and wait for the real DAG scheduler to complete both Agent nodes.
6. Inspect the frozen workflow/workspace/input contract and download the declared deliverable in Run Cockpit.
7. Continue the exact Attempt in AI, ask an external deterministic Agent to read the public run artifact, and receive `PUBLIC_ARTIFACT_READ_OK` from artifact-grounded context.
8. Navigate back to the exact Task Run and verify desktop/mobile bounded layouts.

## Explicit Technical Debt

- `AgentRunCoordinator` currently governs AI Conversation Agent subprocesses with global/provider limits and public queue reasons. Workbench Agent Node execution still uses its established runner and must be migrated to the same lease abstraction in a later change; no claim of cross-runner global capacity is made in this release.
